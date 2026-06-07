#!/usr/bin/env python3
"""
Validate an XML instance against a DTD using ONLY the Python standard library.

This is a pure-stdlib validator built on ``xml.parsers.expat``. Expat is a
non-validating parser, but it *parses* the DTD and hands us every element
content model and attribute declaration through its declaration handlers. We
collect those, then enforce them against the instance ourselves. So we get real
DTD validity checking with no third-party dependency (no lxml).

It reports TWO categories of problem:

  1. Well-formedness errors  - the XML itself is broken (bad nesting, undefined
     entity, malformed syntax). These are fatal; expat stops at the first one.
  2. Validity errors         - the XML is well-formed but violates the DTD
     (wrong child elements or order, missing required attribute, value not in an
     enumeration, dangling IDREF, undeclared element/attribute, ...). We report
     EVERY validity error found, each with the path to the offending element.

Usage:
    python validate.py <instance.xml>                 # DTD comes from the doc's DOCTYPE
    python validate.py <instance.xml> <grammar.dtd>   # validate against an explicit DTD
    python validate.py <instance.xml> <grammar.dtd> --json   # machine-readable output

Exit code: 0 if valid, 1 if invalid, 2 on usage/load error.

The whole point: tell the user precisely *why* a document is invalid, in terms
they can act on, so a mapping can be corrected and re-validated.
"""
import argparse
import io
import os
import re
import sys
from xml.parsers import expat
from xml.parsers.expat import model as M


# --------------------------------------------------------------------------- #
# Lexical helpers (XML 1.0 productions, pragmatic subset)
# --------------------------------------------------------------------------- #
# A practical Name / Nmtoken matcher. The full XML Name production allows a huge
# Unicode range; this covers the common ASCII + accented cases and the standard
# name punctuation. It is deliberately lenient rather than wrong.
_NAME_START = r"A-Za-z_:À-˿Ͱ-῿⁰-↏"
_NAME_CHAR = _NAME_START + r"0-9.\-·"
NAME_RE = re.compile(rf"^[{_NAME_START}][{_NAME_CHAR}]*$")
NMTOKEN_RE = re.compile(rf"^[{_NAME_CHAR}]+$")


def is_name(s):
    return bool(NAME_RE.match(s))


def is_nmtoken(s):
    return bool(NMTOKEN_RE.match(s))


# --------------------------------------------------------------------------- #
# Instance tree
# --------------------------------------------------------------------------- #
class Node:
    __slots__ = ("name", "attrs", "children", "has_nonws_text", "has_any_text", "parent")

    def __init__(self, name, attrs, parent):
        self.name = name
        self.attrs = attrs              # dict: already includes DTD-defaulted attrs
        self.children = []              # list[Node] in document order
        self.has_nonws_text = False     # any non-whitespace character data?
        self.has_any_text = False       # any character data at all (incl. ws)?
        self.parent = parent

    def path(self):
        parts, n = [], self
        while n is not None:
            idx = ""
            if n.parent is not None:
                sibs = [c for c in n.parent.children if c.name == n.name]
                if len(sibs) > 1:
                    idx = f"[{sibs.index(n) + 1}]"
            parts.append(n.name + idx)
            n = n.parent
        return "/" + "/".join(reversed(parts))


# --------------------------------------------------------------------------- #
# DTD model -> regex over element-name alphabet
# --------------------------------------------------------------------------- #
# Expat gives each element's content model as a nested tuple:
#   (ctype, quant, name, (children...))
# ctype in {EMPTY, ANY, MIXED, NAME, CHOICE, SEQ}; quant in {NONE, OPT, REP, PLUS}.
# We translate element-content models into a regex over a private alphabet where
# every distinct child element name maps to one character, then match the actual
# child-name sequence against it. This handles arbitrary nesting of seq/choice
# with ?, * and + exactly the way a validating parser would.
_QUANT = {M.XML_CQUANT_NONE: "", M.XML_CQUANT_OPT: "?",
          M.XML_CQUANT_REP: "*", M.XML_CQUANT_PLUS: "+"}


class ContentModel:
    def __init__(self, model):
        self.ctype = model[0]
        self.raw = model
        self.alphabet = {}          # element name -> single-char token
        self.allowed_children = set()
        self.regex = None
        if self.ctype in (M.XML_CTYPE_CHOICE, M.XML_CTYPE_SEQ, M.XML_CTYPE_NAME):
            pattern = self._compile(model)
            self.regex = re.compile("^" + pattern + "$")
        elif self.ctype == M.XML_CTYPE_MIXED:
            # Mixed content: (#PCDATA | a | b | c)* — children any order/count.
            for child in (model[3] or ()):
                self._token(child[2])
            self.allowed_children = set(self.alphabet)

    def _token(self, name):
        if name not in self.alphabet:
            # Use private-use area code points as single-char tokens.
            self.alphabet[name] = chr(0xE000 + len(self.alphabet))
            self.allowed_children.add(name)
        return self.alphabet[name]

    def _compile(self, model):
        ctype, quant, name, children = model
        if ctype == M.XML_CTYPE_NAME:
            return self._token(name) + _QUANT[quant]
        joiner = "|" if ctype == M.XML_CTYPE_CHOICE else ""
        inner = joiner.join(self._compile(c) for c in (children or ()))
        return "(?:" + inner + ")" + _QUANT[quant]

    def encode(self, child_names):
        """Map a list of child element names to the token string, or None if a
        name isn't part of this model's alphabet (=> not allowed here)."""
        out = []
        for nm in child_names:
            tok = self.alphabet.get(nm)
            if tok is None:
                return None
            out.append(tok)
        return "".join(out)


# --------------------------------------------------------------------------- #
# DTD declarations collector
# --------------------------------------------------------------------------- #
class DTD:
    def __init__(self):
        self.elements = {}          # name -> raw expat model tuple
        self.models = {}            # name -> ContentModel (lazy)
        self.attlists = {}          # elname -> {attname: AttDecl}
        self.root_name = None       # from DOCTYPE
        self.declared = False       # did we see any DTD content at all?

    def model_for(self, name):
        if name not in self.models and name in self.elements:
            self.models[name] = ContentModel(self.elements[name])
        return self.models.get(name)


class AttDecl:
    __slots__ = ("att_type", "default", "required", "enum")

    def __init__(self, att_type, default, required):
        # att_type: 'CDATA','ID','IDREF','IDREFS','NMTOKEN','NMTOKENS','ENTITY',
        #           'ENTITIES','NOTATION', or an enumeration string like '(a|b)'.
        # required: True for #REQUIRED and #FIXED; default holds the #FIXED value.
        self.att_type = att_type
        self.default = default
        self.required = required
        self.enum = None
        m = re.search(r"\(([^)]*)\)", att_type or "")
        if m:
            self.enum = [t.strip() for t in m.group(1).split("|")]

    @property
    def kind(self):
        t = (self.att_type or "").strip()
        if t.startswith("("):
            return "ENUM"
        if t.startswith("NOTATION"):
            return "NOTATION"
        return t

    @property
    def fixed(self):
        return self.required and self.default is not None


# --------------------------------------------------------------------------- #
# Parsing: one expat pass collects DTD decls AND builds the instance tree
# --------------------------------------------------------------------------- #
def _install_decl_handlers(parser, dtd):
    def element_decl(name, model):
        dtd.elements[name] = model
        dtd.declared = True

    def attlist_decl(elname, attname, att_type, default, required):
        dtd.attlists.setdefault(elname, {})[attname] = AttDecl(att_type, default, required)
        dtd.declared = True

    parser.ElementDeclHandler = element_decl
    parser.AttlistDeclHandler = attlist_decl


def parse(instance_path, dtd_path):
    """Parse the instance (optionally with an explicit external DTD) in a single
    expat pass. Returns (root Node, DTD, wf_error_or_None)."""
    dtd = DTD()
    with open(instance_path, "rb") as f:
        raw = f.read()

    # If an explicit DTD was given, inject a DOCTYPE pointing at it (replacing any
    # DOCTYPE already present) so expat loads it as the external subset. This keeps
    # parameter entities and conditional sections working, which an internal-subset
    # injection would not.
    if dtd_path:
        text = raw.decode("utf-8", errors="replace")
        text = re.sub(r"<!DOCTYPE[^>\[]*(\[[^\]]*\])?\s*>", "", text, count=1, flags=re.S)
        root_guess = _first_element_name(text)
        if root_guess is None:
            return None, dtd, "no element found in instance"
        abs_dtd = os.path.abspath(dtd_path).replace("\\", "/")
        doctype = f'<!DOCTYPE {root_guess} SYSTEM "file://{abs_dtd}">\n'
        text = _insert_after_xml_decl(text, doctype)
        raw = text.encode("utf-8")
        base_dir = os.path.dirname(os.path.abspath(dtd_path))
    else:
        base_dir = os.path.dirname(os.path.abspath(instance_path))

    root_holder = {"root": None}
    stack = []

    p = expat.ParserCreate()
    p.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_ALWAYS)
    _install_decl_handlers(p, dtd)

    def start_doctype(name, sysid, pubid, has_internal):
        dtd.root_name = name
    p.StartDoctypeDeclHandler = start_doctype

    def start_element(name, attrs):
        parent = stack[-1] if stack else None
        node = Node(name, dict(attrs), parent)
        if parent is None:
            root_holder["root"] = node
        else:
            parent.children.append(node)
        stack.append(node)
    p.StartElementHandler = start_element

    def end_element(name):
        stack.pop()
    p.EndElementHandler = end_element

    def char_data(data):
        if not stack:
            return
        node = stack[-1]
        node.has_any_text = True
        if data.strip():
            node.has_nonws_text = True
    p.CharacterDataHandler = char_data

    def external_ref(context, base, sysid, pubid):
        # Load an external DTD subset (or external parameter entity) from disk.
        if sysid is None:
            return 1
        path = sysid
        if path.startswith("file://"):
            path = path[len("file://"):]
        if not os.path.isabs(path):
            path = os.path.join(base or base_dir, path)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return 0  # signals a parse error -> reported as well-formedness issue
        sub = p.ExternalEntityParserCreate(context)
        _install_decl_handlers(sub, dtd)
        sub.Parse(data, True)
        return 1
    p.ExternalEntityRefHandler = external_ref

    try:
        p.Parse(raw, True)
    except expat.ExpatError as e:
        msg = f"{expat.ErrorString(e.code)} at line {e.lineno}, column {e.offset}"
        return root_holder["root"], dtd, msg

    return root_holder["root"], dtd, None


def _insert_after_xml_decl(text, insertion):
    m = re.match(r"\s*<\?xml[^>]*\?>", text)
    if m:
        return text[:m.end()] + "\n" + insertion + text[m.end():]
    return insertion + text


def _first_element_name(text):
    # Skip comments / PIs / doctype leftovers, find the first start tag's name.
    for m in re.finditer(r"<([A-Za-z_:][\w.\-:]*)", text):
        start = m.start()
        # ignore things like <?xml, <!--, <!DOCTYPE
        if text[start:start + 2] in ("<?", "<!"):
            continue
        return m.group(1)
    return None


# --------------------------------------------------------------------------- #
# Validation against the collected DTD
# --------------------------------------------------------------------------- #
class Finding:
    def __init__(self, location, rule, message):
        self.location = location
        self.rule = rule
        self.message = message

    def as_dict(self):
        return {"location": self.location, "rule": self.rule, "message": self.message}


def validate_tree(root, dtd):
    findings = []
    ids_defined = {}     # id value -> node path (for uniqueness)
    idrefs = []          # (value, node path, attname)

    def visit(node):
        decl_model = dtd.model_for(node.name)
        if decl_model is None:
            findings.append(Finding(
                node.path(), "undeclared-element",
                f"element <{node.name}> is not declared in the DTD"))
            # Still descend so we surface deeper problems too.
            for c in node.children:
                visit(c)
            return

        _check_content(node, decl_model, findings)
        _check_attrs(node, dtd, findings, ids_defined, idrefs)
        for c in node.children:
            visit(c)

    visit(root)

    # Cross-references: every IDREF/IDREFS token must point at a declared ID.
    for value, path, attname in idrefs:
        for tok in value.split():
            if tok not in ids_defined:
                findings.append(Finding(
                    path, "idref-unresolved",
                    f"attribute '{attname}' references id '{tok}' but no element "
                    f"has that ID"))
    return findings


def _check_content(node, cm, findings):
    ctype = cm.ctype
    child_names = [c.name for c in node.children]

    if ctype == M.XML_CTYPE_EMPTY:
        if node.children:
            findings.append(Finding(
                node.path(), "content-empty",
                f"<{node.name}> is declared EMPTY but contains child element(s): "
                f"{', '.join(dict.fromkeys(child_names))}"))
        if node.has_any_text:
            findings.append(Finding(
                node.path(), "content-empty",
                f"<{node.name}> is declared EMPTY but contains character data"))
        return

    if ctype == M.XML_CTYPE_ANY:
        return  # any well-formed content is allowed

    if ctype == M.XML_CTYPE_MIXED:
        if node.children:
            bad = [n for n in child_names if n not in cm.allowed_children]
            if bad:
                allowed = ", ".join(sorted(cm.allowed_children)) or "(none)"
                findings.append(Finding(
                    node.path(), "content-mixed",
                    f"<{node.name}> (mixed content) contains element(s) not "
                    f"allowed here: {', '.join(dict.fromkeys(bad))}. Allowed: {allowed}"))
        return

    # Element content (SEQ / CHOICE / single NAME): text not allowed, order matters.
    if node.has_nonws_text:
        findings.append(Finding(
            node.path(), "content-element",
            f"<{node.name}> has element content; non-whitespace text is not allowed"))
    encoded = cm.encode(child_names)
    if encoded is None or not cm.regex.match(encoded):
        expected = _describe_model(cm.raw)
        got = ", ".join(child_names) if child_names else "(empty)"
        findings.append(Finding(
            node.path(), "content-element",
            f"<{node.name}> children do not match its content model. "
            f"Expected: {expected}. Got: {got}"))


def _check_attrs(node, dtd, findings, ids_defined, idrefs):
    decls = dtd.attlists.get(node.name, {})
    path = node.path()

    # Attributes present that the DTD never declared for this element.
    for attname in node.attrs:
        if attname in decls:
            continue
        if attname == "xmlns" or attname.startswith("xmlns:"):
            continue  # namespace declarations predate/transcend DTDs; don't flag
        findings.append(Finding(
            path, "undeclared-attribute",
            f"attribute '{attname}' on <{node.name}> is not declared in the DTD"))

    for attname, d in decls.items():
        present = attname in node.attrs
        value = node.attrs.get(attname)

        if not present:
            if d.required and not d.fixed:
                findings.append(Finding(
                    path, "attr-required",
                    f"<{node.name}> is missing #REQUIRED attribute '{attname}'"))
            continue

        if d.fixed and value != d.default:
            findings.append(Finding(
                path, "attr-fixed",
                f"attribute '{attname}' is #FIXED to \"{d.default}\" but is "
                f"\"{value}\""))

        kind = d.kind
        if kind == "ENUM" or kind == "NOTATION":
            if d.enum is not None and value not in d.enum:
                findings.append(Finding(
                    path, "attr-enum",
                    f"attribute '{attname}'=\"{value}\" is not one of the allowed "
                    f"values: {', '.join(d.enum)}"))
        elif kind in ("NMTOKEN",):
            if not is_nmtoken(value):
                findings.append(Finding(
                    path, "attr-nmtoken",
                    f"attribute '{attname}'=\"{value}\" is not a valid NMTOKEN"))
        elif kind in ("NMTOKENS",):
            for tok in value.split():
                if not is_nmtoken(tok):
                    findings.append(Finding(
                        path, "attr-nmtokens",
                        f"attribute '{attname}' token \"{tok}\" is not a valid NMTOKEN"))
        elif kind == "ID":
            if not is_name(value):
                findings.append(Finding(
                    path, "attr-id",
                    f"ID attribute '{attname}'=\"{value}\" is not a valid XML Name"))
            elif value in ids_defined:
                findings.append(Finding(
                    path, "id-duplicate",
                    f"ID value \"{value}\" is already used at {ids_defined[value]}; "
                    f"IDs must be unique"))
            else:
                ids_defined[value] = path
        elif kind == "IDREF":
            idrefs.append((value, path, attname))
        elif kind == "IDREFS":
            idrefs.append((value, path, attname))
        # CDATA / ENTITY / ENTITIES: no lexical constraint we can fully check here.


def _describe_model(model):
    """Render an expat content-model tuple back into DTD-ish notation for messages."""
    ctype, quant, name, children = model
    q = _QUANT.get(quant, "")
    if ctype == M.XML_CTYPE_EMPTY:
        return "EMPTY"
    if ctype == M.XML_CTYPE_ANY:
        return "ANY"
    if ctype == M.XML_CTYPE_NAME:
        return name + q
    if ctype == M.XML_CTYPE_MIXED:
        names = "|".join(["#PCDATA"] + [c[2] for c in (children or ())])
        return f"({names})*"
    sep = " | " if ctype == M.XML_CTYPE_CHOICE else ", "
    inner = sep.join(_describe_model(c) for c in (children or ()))
    return f"({inner}){q}"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description="Validate an XML instance against a DTD (pure Python stdlib).")
    ap.add_argument("instance", help="Path to the XML instance file")
    ap.add_argument("dtd", nargs="?", default=None,
                    help="Path to the DTD file (optional if the instance has a DOCTYPE)")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="Emit machine-readable JSON instead of text")
    args = ap.parse_args()

    if not os.path.exists(args.instance):
        print(f"ERROR: file not found: {args.instance}", file=sys.stderr)
        sys.exit(2)
    if args.dtd and not os.path.exists(args.dtd):
        print(f"ERROR: file not found: {args.dtd}", file=sys.stderr)
        sys.exit(2)

    root, dtd, wf_error = parse(args.instance, args.dtd)

    if wf_error:
        _emit(args.as_json, valid=False, wf_error=wf_error, findings=[])
        sys.exit(1)

    if root is None:
        _emit(args.as_json, valid=False,
              wf_error="no document element found", findings=[])
        sys.exit(1)

    if not dtd.declared:
        msg = ("no DTD declarations were found. Provide a DTD file argument, or "
               "ensure the instance has a <!DOCTYPE ...> referencing one.")
        _emit(args.as_json, valid=False, wf_error=msg, findings=[])
        sys.exit(2)

    findings = []
    if dtd.root_name and root.name != dtd.root_name:
        findings.append(Finding(
            root.path(), "root-mismatch",
            f"document element is <{root.name}> but the DOCTYPE names "
            f"<{dtd.root_name}> as the root"))
    findings.extend(validate_tree(root, dtd))

    _emit(args.as_json, valid=not findings, wf_error=None, findings=findings)
    sys.exit(0 if not findings else 1)


def _emit(as_json, valid, wf_error, findings):
    if as_json:
        import json
        print(json.dumps({
            "valid": valid,
            "wellFormednessError": wf_error,
            "errorCount": (0 if valid else (1 if wf_error else len(findings))),
            "errors": [f.as_dict() for f in findings],
        }, indent=2))
        return

    if wf_error:
        print("NOT WELL-FORMED — the XML could not be parsed:")
        print(f"  {wf_error}")
        print("\nFix the syntax/structure before DTD validity can be checked.")
        return
    if valid:
        print("VALID — the document is well-formed and conforms to the DTD.")
        return
    print(f"INVALID — {len(findings)} validity error(s) found:\n")
    for i, f in enumerate(findings, 1):
        print(f"{i}. at {f.location}")
        print(f"   rule   : {f.rule}")
        print(f"   problem: {f.message}")
        print()


if __name__ == "__main__":
    main()
