import os
import re

from lxml import etree
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, backref, object_session
from sqlalchemy import (PickleType, Numeric, Unicode,
                        Column, Integer, ForeignKey,
                        UnicodeText, Boolean, event)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pyteomics import mass

Base = declarative_base()

_unimod_xml_download_url = "http://www.unimod.org/xml/unimod_tables.xml"

try:
    basestring
except:
    basestring = (str, bytes)


def simple_repr(self):
    template = "{self.__class__.__name__}({d})"
    d = {"%s=%r" % (k, v) for k, v in self.__dict__.items() if not k.startswith("_")}
    return template.format(self=self, d=', '.join(d))

Base.__repr__ = simple_repr


def remove_namespace(doc, namespace):
    """Remove namespace in the passed document in place."""
    ns = u'{%s}' % namespace
    nsl = len(ns)
    for elem in doc.getiterator():
        if elem.tag.startswith(ns):
            elem.tag = elem.tag[nsl:]


def preprocess_xml(doc_path):
    tree = etree.parse(doc_path)
    root = tree.getroot()
    for ns in root.nsmap.values():
        remove_namespace(tree, ns)
    return tree


def _formula_parser(formula, session):
    '''
    Parse a unimod formula composed of elements,
    isotopes, and other bricks.

    In order to look up a Brick's composition, this function must have access to a session.
    '''
    composition = mass.Composition()
    for token in formula.split(" "):
        match = re.search(r"(?P<isotope>\d+?)?(?P<elemet>[^\(]+)(?:\((?P<count>-?\d+)\))?", token)
        if match:
            isotope, element, count = match.groups()
            if count is not None:
                count = int(count)
            else:
                count = 1
            if isotope is not None:
                name = mass._make_isotope_string(element, isotope)
            else:
                name = element
            is_brick = session.query(Brick).filter(Brick.brick == name).first()
            if is_brick is None:
                composition[name] += count
            else:
                composition += is_brick.composition * count
    return composition


def _composition_listener(attr):
    '''
    Attach event listeners to an InstrumentedAttribute
    to trigger formula parsing on load and on change.
    '''
    @event.listens_for(attr, "set")
    def _update_composition_from_formula(target, value, oldvalue, initiator):
        session = object_session(target)
        if value == "" or value is None:
            return
        # If the object hasn't been associated with a session,
        # we can't look up bricks.
        if session is None:
            return
        target.composition = _formula_parser(value, session)

    @event.listens_for(attr.class_, "load")
    def _update_composition_on_load(target, context):
        value = getattr(target, attr.prop.key)
        if value == "" or value is None:
            return
        session = object_session(target)
        target.composition = _formula_parser(value, session)


def has_composition(attr_name):
    '''
    A decorator to simplify flagging a Model with a column
    to be treated as a formula for parsing. Calls :func:`_composition_listener`
    internally.
    '''
    def decorator(model):
        _composition_listener(getattr(model, attr_name))
        return model
    return decorator


class AlternativeName(Base):
    __tablename__ = "AlternativeName"

    _tag_name = "alt_names_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib["record_id"]),
            alt_name=attrib['alt_name'],
            modification_id=int(attrib['mod_key'])
            )
        return inst

    id = Column(Integer, primary_key=True)
    alt_name = Column(Unicode(256), index=True)
    modification_id = Column(Integer, ForeignKey("Modification.id"), index=True)


class AminoAcid(Base):
    __tablename__ = "AminoAcid"

    _tag_name = "amino_acids_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            full_name=attrib['full_name'],
            one_letter=attrib['one_letter'],
            three_letter=attrib['three_letter'],
            num_H=int(attrib["num_H"]),
            num_O=int(attrib["num_O"]),
            num_C=int(attrib["num_C"]),
            num_N=int(attrib["num_N"]),
            num_S=int(attrib["num_S"]),
            )
        return inst

    id = Column(Integer, primary_key=True)
    num_H = Column(Integer)
    num_O = Column(Integer)
    num_C = Column(Integer)
    num_N = Column(Integer)
    num_S = Column(Integer)
    full_name = Column(Unicode(25), index=True)
    one_letter = Column(Unicode(10), index=True)
    three_letter = Column(Unicode(10), index=True)


class Classification(Base):
    __tablename__ = "Classification"

    _tag_name = "classifications_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            classification=attrib["classification"]
            )
        return inst

    id = Column(Integer, primary_key=True)
    classification = Column(Unicode(30), index=True)


class Position(Base):
    __tablename__ = "Position"

    _tag_name = "positions_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            position=attrib['position']
            )
        return inst

    id = Column(Integer, primary_key=True)
    position = Column(Unicode(20), index=True)


class Brick(Base):
    __tablename__ = "Brick"

    _tag_name = "bricks_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            brick=attrib['brick'],
            full_name=attrib['full_name']
            )
        return inst

    id = Column(Integer, primary_key=True)
    brick = Column(Unicode(64), index=True)
    full_name = Column(Unicode(128), index=True)

    elements = relationship("BrickToElement")

    @property
    def composition(self):
        composition = mass.Composition()
        for element_relation in self.elements:
            symbol = element_relation.element
            isotope, element = re.search(r"(?P<isotope>\d*)?(?P<element>\S+)", symbol).groups()
            if isotope != "":
                isotope = int(isotope)
                iso_str = mass._make_isotope_string(element, isotope)
            else:
                iso_str = element
            count = element_relation.count
            composition[iso_str] = count
        return composition


class Fragment(Base):
    __tablename__ = "Fragment"

    _tag_name = "fragments_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            modification_id=int(attrib["mod_key"])
            )
        return inst

    id = Column(Integer, primary_key=True)
    modification_id = Column(Integer, ForeignKey("Modification.id"), index=True)


class FragmentComposition(Base):
    __tablename__ = "FragmentComposition"

    _tag_name = "fragment_comp_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            brick_string=attrib["brick"],
            fragment_id=int(attrib["fragments_key"]),
            count=int(attrib["num_brick"])
            )
        return inst

    id = Column(Integer, primary_key=True)
    brick_string = Column(Unicode(64), ForeignKey(Brick.brick), index=True)
    fragment_id = Column(Integer, ForeignKey("Fragment.id"), index=True)
    count = Column(Integer)


class ModificationToBrick(Base):
    __tablename__ = "ModificationToBrick"

    _tag_name = "mod2brick_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            brick_string=(attrib['brick']),
            modification_id=int(attrib["mod_key"]),
            count=int(attrib["num_brick"])
            )
        return inst

    id = Column(Integer, primary_key=True)
    brick_string = Column(Unicode(64), ForeignKey(Brick.brick), index=True)
    modification_id = Column(Integer, ForeignKey("Modification.id"), index=True)
    count = Column(Integer)


class BrickToElement(Base):
    __tablename__ = "BrickToElement"

    _tag_name = "brick2element_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            brick_id=int(attrib['brick_key']),
            count=int(attrib["num_element"]),
            element=attrib['element']
            )
        return inst

    id = Column(Integer, primary_key=True)
    brick_id = Column(Integer, ForeignKey(Brick.id), index=True)
    element = Column(Unicode(16), ForeignKey("Element.element"), index=True)
    element_obj = relationship("Element", uselist=False)
    count = Column(Integer)


class Element(Base):
    __tablename__ = "Element"

    _tag_name = "elements_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            average_mass=float(attrib["avge_mass"]),
            monoisotopic_mass=float(attrib["mono_mass"]),
            full_name=attrib["full_name"],
            element=attrib["element"]

            )
        return inst

    id = Column(Integer, primary_key=True)
    average_mass = Column(Numeric(12, 6, asdecimal=False))
    monoisotopic_mass = Column(Numeric(12, 6, asdecimal=False))
    full_name = Column(Unicode(64), index=True)
    element = Column(Unicode(16), index=True)


@has_composition("_composition")
class Modification(Base):
    __tablename__ = "Modification"

    _tag_name = "modifications_row"

    id = Column(Integer, primary_key=True)
    username_of_poster = Column(Unicode(128))
    average_mass = Column(Numeric(12, 6, asdecimal=False), index=True)
    ex_code_name = Column(Unicode(64), index=True)
    monoisotopic_mass = Column(Numeric(12, 6, asdecimal=False), index=True)
    full_name = Column(Unicode(128), index=True)
    code_name = Column(Unicode(128), index=True)
    _composition = Column(Unicode(128), index=True)
    approved = Column(Boolean, index=True)

    notes = relationship("MiscNotesModifications")
    specificities = relationship("Specificty")
    bricks = relationship(ModificationToBrick)
    alternative_names = relationship(AlternativeName, backref=backref("modification"))
    fragments = relationship(Fragment)

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib["record_id"]),
            username_of_poster=attrib["username_of_poster"],
            average_mass=float(attrib["avge_mass"]),
            monoisotopic_mass=float(attrib["mono_mass"]),
            ex_code_name=attrib["ex_code_name"],
            code_name=attrib["code_name"],
            full_name=attrib["full_name"],
            approved=bool(int(attrib['approved'])),
            _composition=attrib["composition"]
            )
        for note in tag:
            if note.tag == MiscNotesModifications._tag_name:
                model_note = MiscNotesModifications._from_tag(note, inst.id)
                if model_note is not None:
                    inst.notes.append(model_note)
        return inst


class MiscNotesModifications(Base):
    __tablename__ = "MiscNotesModifications"
    _tag_name = "misc_notes"

    id = Column(Integer, primary_key=True)
    modification_id = Column(Integer, ForeignKey(Modification.id), index=True)
    text = Column(UnicodeText)

    @classmethod
    def _from_tag(cls, tag, modification_id):
        if tag.text is None:
            return
        return cls(text=tag.text, modification_id=modification_id)


class Specificty(Base):
    __tablename__ = "Specificty"

    _tag_name = "specificity_row"

    id = Column(Integer, primary_key=True)
    position_id = Column(Integer, ForeignKey(Position.id), index=True)
    classification_id = Column(Integer, ForeignKey(Classification.id), index=True)
    classification = relationship("Classification", uselist=False)
    # Map through one_letter
    amino_acid = Column(Unicode(10), ForeignKey(AminoAcid.one_letter), index=True)
    modification_id = Column(Integer, ForeignKey(Modification.id), index=True)
    hidden = Column(Boolean, index=True)
    group = Column(Integer, index=True)
    neutral_losses = relationship("SpecificityToNeutralLoss")

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            position_id=int(attrib['position_key']),
            classification_id=int(attrib["classifications_key"]),
            hidden=bool(int(attrib["hidden"])),
            amino_acid=attrib["one_letter"],
            modification_id=int(attrib["mod_key"]),
            )
        return inst


class NeutralLoss(Base):
    __tablename__ = "NeutralLoss"

    _tag_name = "neutral_losses_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            brick_string=(attrib['brick']),
            count=int(attrib["num_brick"]),
            specificity_id=int(attrib["spec_key"])
            )
        return inst

    id = Column(Integer, primary_key=True)
    brick_string = Column(Unicode(64), index=True)
    specificity_id = Column(Integer, ForeignKey(Specificty.id), index=True)
    count = Column(Integer)


@has_composition("_composition")
class SpecificityToNeutralLoss(Base):
    __tablename__ = "SpecificityToNeutralLoss"

    _tag_name = "spec2nl_row"

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls(
            id=int(attrib['record_id']),
            specificity_id=int(attrib["spec_key"]),
            monoisotopic_mass=float(attrib["nl_mono_mass"]),
            average_mass=float(attrib["nl_avge_mass"]),
            is_required_peptide_neutral_loss=bool(int(attrib["is_req_pep_nl"])),
            is_peptide_neutral_loss=bool(int(attrib["is_pep_nl"])),
            is_slave=bool(int(attrib["is_slave_nl"])),
            _composition=attrib['nl_composition']
            )
        return inst

    id = Column(Integer, primary_key=True)
    specificity_id = Column(Integer, ForeignKey(Specificty.id), index=True)
    specificity = relationship(Specificty, uselist=False)
    monoisotopic_mass = Column(Numeric(12, 6, asdecimal=False), index=True)
    average_mass = Column(Numeric(12, 6, asdecimal=False), index=True)
    _composition = Column(Unicode(128))
    is_slave = Column(Boolean, index=True)
    is_peptide_neutral_loss = Column(Boolean, index=True)
    is_required_peptide_neutral_loss = Column(Boolean, index=True)


class CrossreferenceSource(Base):
    __tablename__ = "CrossreferenceSource"
    _tag_name = "xref_sources_row"

    id = Column(Integer, primary_key=True)
    source = Column(Unicode(64), index=True)

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls()
        inst.id = int(attrib['record_id'])
        inst.source = attrib['xref_source']
        return inst


class Crossreference(Base):
    __tablename__ = "Crossreference"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey(CrossreferenceSource.id), index=True)
    url = Column(Unicode(128))
    modification_id = Column(Integer, ForeignKey(Modification.id), index=True)
    text = Column(UnicodeText)

    @classmethod
    def from_tag(cls, tag):
        attrib = tag.attrib
        inst = cls()
        inst.id = int(attrib['record_id'])
        inst.url = attrib['xref_url']
        inst.source_id = int(attrib['xref_source_key'])
        inst.modification_id = int(attrib["mod_key"])
        text = []
        for node in tag.getchildren():
            if node.tag == "xref_text":
                if node.text is not None:
                    text.append(node.text)
        inst.text = "\n".join(text)
        return inst


def load(doc_path, output_path="sqlite://"):
    '''
    Parse the relational table-like XML file provided by http://www.unimod.org/downloads.html
    and convert each <tag>_row into an equivalent database entry.

    By default the table will be held in memory.
    '''
    tree = preprocess_xml(doc_path)
    engine = create_engine(output_path)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    for model in Base._decl_class_registry.values():
        if hasattr(model, "_tag_name") and hasattr(model, "from_tag"):
            for tag in tree.iterfind(".//" + model._tag_name):
                session.add(model.from_tag(tag))
        session.commit()
    return session


def session(path="sqlite:///unimod.db"):
    engine = create_engine(path)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    return session


class Unimod(object):
    def __init__(self, path=None):
        if path is None:
            self.path = None
            self.session = load(_unimod_xml_download_url)
        else:
            self.path = path
            try:
                self.session = session(path)
                self.session.query(Modification).first()
            except:
                # Database may not yet exist at that location
                self.session = load(_unimod_xml_download_url, path)
                self.session.query(Modification).first()

    def get(self, identifier):
        if isinstance(identifier, int):
            mod = self.session.query(Modification).get(identifier)
            if mod is None:
                raise KeyError(identifier)
            return mod
        elif isinstance(identifier, basestring):
            mod = self.session.query(Modification).filter(
                (Modification.full_name == identifier) |
                (Modification.code_name == identifier) |
                (Modification.ex_code_name == identifier)).first()
            if mod is None:
                alt_name = self.session.query(AlternativeName).filter(
                    AlternativeName.alt_name == identifier).first()
                if alt_name is None:
                    raise KeyError(identifier)
                mod = alt_name.modification
            return mod

    def by_name(self, name, strict=True):
        if strict:
            mod = self.session.query(Modification).filter(
                (Modification.full_name == name) |
                (Modification.code_name == name) |
                (Modification.ex_code_name == name)).first()
            if mod is None:
                alt_name = self.session.query(AlternativeName).filter(
                    AlternativeName.alt_name == name).first()
                if alt_name is None:
                    raise KeyError(name)
                mod = alt_name.modification
            return mod
        else:
            qname = "%%%s%%" % name
            mod = self.session.query(Modification).filter(
                (Modification.full_name.like(qname)) |
                (Modification.code_name.like(qname)) |
                (Modification.ex_code_name.like(qname))).first()
            if mod is None:
                alt_name = self.session.query(AlternativeName).filter(
                    AlternativeName.alt_name.like(qname)).first()
                if alt_name is None:
                    raise KeyError(name)
                mod = alt_name.modification
            return mod

    by_title = by_name

    __getitem__ = get

    @property
    def mods(self):
        return self.session.query(Modification).all()
