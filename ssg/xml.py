from __future__ import absolute_import
from __future__ import print_function

import platform
import re
import xml.etree.ElementTree as ET

from .constants import (
    xml_version, oval_header, timestamp, PREFIX_TO_NS, XCCDF11_NS, XCCDF12_NS)
from .constants import (
    datastream_namespace,
    oval_namespace,
    cat_namespace,
    xlink_namespace,
    ocil_namespace,
    cpe_language_namespace,
)


try:
    from xml.etree import cElementTree as ElementTree
except ImportError:
    from xml.etree import ElementTree as ElementTree


def oval_generated_header(product_name, schema_version, ssg_version):
    return xml_version + oval_header + \
        """
    <generator>
        <oval:product_name>%s from SCAP Security Guide</oval:product_name>
        <oval:product_version>ssg: %s, python: %s</oval:product_version>
        <oval:schema_version>%s</oval:schema_version>
        <oval:timestamp>%s</oval:timestamp>
    </generator>""" % (product_name, ssg_version, platform.python_version(),
                       schema_version, timestamp)


def register_namespaces():
    """
    Register all possible namespaces
    """
    try:
        for prefix, uri in PREFIX_TO_NS.items():
            ElementTree.register_namespace(prefix, uri)
    except Exception:
        # Probably an old version of Python
        # Doesn't matter, as this is non-essential.
        pass


def open_xml(filename):
    """
    Given a filename, register all possible namespaces, and return the XML tree.
    """
    register_namespaces()
    return ElementTree.parse(filename)


def parse_file(filename):
    """
    Given a filename, return the root of the ElementTree
    """
    tree = open_xml(filename)
    return tree.getroot()


def map_elements_to_their_ids(tree, xpath_expr):
    """
    Given an ElementTree and an XPath expression,
    iterate through matching elements and create 1:1 id->element mapping.

    Raises AssertionError if a matching element doesn't have the ``id``
    attribute.

    Returns mapping as a dictionary
    """
    aggregated = {}
    for element in tree.findall(xpath_expr):
        element_id = element.get("id")
        assert element_id is not None
        aggregated[element_id] = element
    return aggregated


SSG_XHTML_TAGS = [
    'table', 'tr', 'th', 'td', 'ul', 'li', 'ol',
    'p', 'code', 'strong', 'b', 'em', 'i', 'pre', 'br', 'hr', 'small',
]


def add_xhtml_namespace(data):
    """
    Given a xml blob, adds the xhtml namespace to all relevant tags.
    """
    # The use of lambda in the lines below is a workaround for https://bugs.python.org/issue1519638
    # I decided for this approach to avoid adding workarounds in the matching regex, this way only
    # the substituted part contains the workaround.
    # Transform <tt> in <code>
    data = re.sub(r'<(\/)?tt(\/)?>',
                  lambda m: r'<' + (m.group(1) or '') + 'code' + (m.group(2) or '') + '>', data)
    # Adds xhtml prefix to elements: <tag>, </tag>, <tag/>
    return re.sub(r'<(\/)?((?:%s).*?)(\/)?>' % "|".join(SSG_XHTML_TAGS),
                  lambda m: r'<' + (m.group(1) or '') + 'xhtml:' +
                  (m.group(2) or '') + (m.group(3) or '') + '>',
                  data)


def determine_xccdf_tree_namespace(tree):
    root = tree.getroot()
    if root.tag == "{%s}Benchmark" % XCCDF11_NS:
        xccdf_ns = XCCDF11_NS
    elif root.tag == "{%s}Benchmark" % XCCDF12_NS:
        xccdf_ns = XCCDF12_NS
    else:
        raise ValueError("Unknown root element '%s'" % root.tag)
    return xccdf_ns


def get_element_tag_without_ns(xml_tag):
    return re.search(r'^{.*}(.*)', xml_tag).group(1)


def get_element_namespace(self):
    return re.search(r'^{(.*)}.*', self.root.tag).group(1)


class XMLElement(object):
    '''
    Represents an generic element read from an XML file.
    '''
    ns = {
        "ds": datastream_namespace,
        "xccdf-1.1": XCCDF11_NS,
        "xccdf-1.2": XCCDF12_NS,
        "oval": oval_namespace,
        "catalog": cat_namespace,
        "xlink": xlink_namespace,
        "ocil": ocil_namespace,
        "cpe-lang": cpe_language_namespace,
    }

    def __init__(self, root):
        self.root = root
        self._determine_xccdf_version()

    def get_attr(self, attr):
        return self.root.get(attr)

    def get_namespace(self):
        return re.search(r'^{(.*)}.*', self.root.tag).group(1)

    def _determine_xccdf_version(self):
        if self.get_namespace() == self.ns["xccdf-1.1"]:
            self.content_xccdf_ns = "xccdf-1.1"
        else:
            self.content_xccdf_ns = "xccdf-1.2"


class XMLContent(XMLElement):
    '''
    Can represent a Data Stream or an XCCDF Benchmark read from an XML file.
    '''

    check_engines = [("oval", "oval:oval_definitions"), ("ocil", "ocil:ocil")]

    def __init__(self, root):
        super(XMLContent, self).__init__(root)
        self.component_refs = self.get_component_refs()
        self.uris = self.get_uris()
        self.components = self._find_all_component_contents()

    def get_component_refs(self):
        component_refs = dict()
        for ds in self.root.findall("ds:data-stream", self.ns):
            checks = ds.find("ds:checks", self.ns)
            for component_ref in checks.findall("ds:component-ref", self.ns):
                component_ref_href = component_ref.get("{%s}href" % (self.ns["xlink"]))
                component_ref_id = component_ref.get("id")
                component_refs[component_ref_href] = component_ref_id
        return component_refs

    def get_uris(self):
        uris = dict()
        for ds in self.root.findall("ds:data-stream", self.ns):
            checklists = ds.find("ds:checklists", self.ns)
            catalog = checklists.find(".//catalog:catalog", self.ns)
            for uri in catalog.findall("catalog:uri", self.ns):
                uri_uri = uri.get("uri")
                uri_name = uri.get("name")
                uris[uri_uri] = uri_name
        return uris

    def is_benchmark(self):
        if self.root.tag == "{%s}Benchmark" % (self.ns["xccdf-1.2"]):
            return True
        elif self.root.tag == "{%s}Benchmark" % (self.ns["xccdf-1.1"]):
            self.content_xccdf_ns = "xccdf-1.1"
            return True

    def get_benchmarks(self):
        ds_components = self.root.findall("ds:component", self.ns)
        if not ds_components:
            # The content is not a DS, maybe it is just an XCCDF Benchmark
            if self.is_benchmark():
                yield XMLBenchmark(self.root)
        for component in ds_components:
            for benchmark in component.findall("%s:Benchmark" % self.content_xccdf_ns, self.ns):
                yield XMLBenchmark(benchmark)

    def find_benchmark(self, id_):
        ds_components = self.root.findall("ds:component", self.ns)
        if not ds_components:
            # The content is not a DS, maybe it is just an XCCDF Benchmark
            if self.is_benchmark():
                return XMLBenchmark(self.root)
        for component in ds_components:
            benchmark = component.find("%s:Benchmark[@id='%s']" % (self.content_xccdf_ns, id_), self.ns)
            if benchmark is not None:
                return XMLBenchmark(benchmark)
        return None

    def _find_all_component_contents(self):
        component_doc_dict = dict()
        for component in self.root.findall("ds:component", self.ns):
            for check_spec in self.check_engines:
                def_doc = component.find(check_spec[1], self.ns)
                if def_doc is not None:
                    def_doc_dict = dict()
                    comp_id = component.get("id")
                    comp_href = "#" + comp_id
                    try:
                        filename = self.uris["#" + self.component_refs[comp_href]]
                    except KeyError:
                        continue
                    def_doc_dict[filename] = XMLComponent(def_doc)
                    component_doc_dict[check_spec[0]] = def_doc_dict
                    # This component matched one of the checking engines,
                    # thre is no need to continue further
                    break
        return component_doc_dict


class XMLBenchmark(XMLElement):
    '''
    Represents an XCCDF Benchmark read from an XML file.
    '''

    def __init__(self, root):
        super(XMLBenchmark, self).__init__(root)
        self.root = root

    def find_rules(self, rule_id):
        if rule_id:
            rules = [XMLRule(r) for r in self.root.iterfind(
                ".//%s:Rule[@id='%s']" % (self.content_xccdf_ns, rule_id), self.ns)]
            if len(rules) == 0:
                raise ValueError("Can't find rule %s" % (rule_id))
        else:
            rules = [XMLRule(r) for r in self.root.iterfind(
                ".//%s:Rule" % (self.content_xccdf_ns), self.ns)]
        return rules

    def find_rule(self, rule_id):
        rule = self.root.find(
                ".//%s:Rule[@id='%s']" % (self.content_xccdf_ns, rule_id), self.ns)
        return XMLRule(rule) if rule else None

    def find_all_cpe_platforms(self, idref):
        cpes = [XMLCPEPlatform(p) for p in self.root.iterfind(
            ".//cpe-lang:platform[@id='{0}']".format(idref.replace("#", "")), self.ns)]
        return cpes


class XMLRule(XMLElement):
    '''
    Represents an XCCDF Rule read from an XML file.
    '''

    def __init__(self, root):
        super(XMLRule, self).__init__(root)
        self.root = root

    def get_check_element(self, check_system_uri):
        return self.root.find(
            "%s:check[@system='%s']" % (self.content_xccdf_ns, check_system_uri), self.ns)

    def get_check_content_ref_element(self, check_element):
        return check_element.find(
            "%s:check-content-ref" % (self.content_xccdf_ns), self.ns)

    def get_fix_element(self, remediation_type_uri):
        return self.root.find("%s:fix[@system='%s']" % (self.content_xccdf_ns, remediation_type_uri), self.ns)

    def get_version_element(self):
        return self.root.find("%s:version" % (self.content_xccdf_ns), self.ns)

    def get_all_platform_elements(self):
        return self.root.findall(".//%s:platform" % (self.content_xccdf_ns), self.ns)


class XMLComponent(XMLElement):
    '''
    Represents the element of the Data stream component that has relevant content.

    This make it easier to access contents pertaining to a SCAP component.
    '''
    def __init__(self, root):
        super(XMLComponent, self).__init__(root)

    def find_oval_definition(self, def_id):
        definitions = self.root.find("oval:definitions", self.ns)
        definition = definitions.find("oval:definition[@id='%s']" % (def_id), self.ns)
        return XMLOvalDefinition(definition)

    def find_ocil_questionnaire(self, def_id):
        questionnaires = self.root.find("ocil:questionnaires", self.ns)
        questionnaire = questionnaires.find(
            "ocil:questionnaire[@id='%s']" % def_id, self.ns)
        return XMLOcilQuestionnaire(questionnaire)

    def find_ocil_test_action(self, test_action_ref):
        test_actions = self.root.find("ocil:test_actions", self.ns)
        test_action = test_actions.find(
            "ocil:boolean_question_test_action[@id='%s']" % test_action_ref, self.ns)
        return XMLOcilTestAction(test_action)

    def find_ocil_boolean_question(self, question_id):
        questions = self.root.find("ocil:questions", self.ns)
        question = questions.find(
            "ocil:boolean_question[@id='%s']" % question_id, self.ns)
        return XMLOcilQuestion(question)


class XMLOvalDefinition(XMLComponent):
    def __init__(self, root):
        super(XMLOvalDefinition, self).__init__(root)

    def get_criteria_element(self):
        return self.root.find("oval:criteria", self.ns)


class XMLOcilQuestionnaire(XMLComponent):
    def __init__(self, root):
        super(XMLOcilQuestionnaire, self).__init__(root)

    def get_test_action_ref_element(self):
        return self.root.find(
            "ocil:actions/ocil:test_action_ref", self.ns)

    def get_test_action_ref_element(self):
        return self.root.find(
            "ocil:actions/ocil:test_action_ref", self.ns)


class XMLOcilTestAction(XMLComponent):
    def __init__(self, root):
        super(XMLOcilTestAction, self).__init__(root)


class XMLOcilQuestion(XMLComponent):
    def __init__(self, root):
        super(XMLOcilQuestion, self).__init__(root)

    def get_question_test_element(self):
        return self.root.find("ocil:question_text", self.ns)


class XMLCPEPlatform(XMLElement):
    def __init__(self, root):
        super(XMLCPEPlatform, self).__init__(root)

    def find_all_fact_ref_elements(self):
        return self.root.findall(".//cpe-lang:fact-ref", self.ns)
