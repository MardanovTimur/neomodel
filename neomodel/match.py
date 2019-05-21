# encoding=utf-8
import inspect
import re

from .core import StructuredNode, db
from .properties import AliasProperty
from .exceptions import MultipleNodesReturned, NeomodelException
from .match_q import Q, QBase
from functools import singledispatch, reduce
from .util import get_rhs_ident, UnionBlock

OUTGOING, INCOMING, EITHER = 1, -1, 0


# basestring python 3.x fallback
try:
    basestring
except NameError:
    basestring = str


def _rel_helper(
        lhs,
        rhs,
        ident=None,
        relation_type=None,
        direction=None,
        relation_properties=None,
        **kwargs):
    """
    Generate a relationship matching string, with specified parameters.
    Examples:
    relation_direction = OUTGOING: (lhs)-[relation_ident:relation_type]->(rhs)
    relation_direction = INCOMING: (lhs)<-[relation_ident:relation_type]-(rhs)
    relation_direction = EITHER: (lhs)-[relation_ident:relation_type]-(rhs)

    :param lhs: The left hand statement.
    :type lhs: str
    :param rhs: The right hand statement.
    :type rhs: str
    :param ident: A specific identity to name the relationship, or None.
    :type ident: str
    :param relation_type: None for all direct rels, * for all of any length, or a name of an explicit rel.
    :type relation_type: str
    :param direction: None or EITHER for all OUTGOING,INCOMING,EITHER. Otherwise OUTGOING or INCOMING.
    :param relation_properties: dictionary of relationship properties to match
    :returns: string
    """
    if kwargs.get('reverse'):
        direction = -direction

    if direction == OUTGOING:
        stmt = '-{0}->'
    elif direction == INCOMING:
        stmt = '<-{0}-'
    else:
        stmt = '-{0}-'

    rel_props = ''

    if relation_properties:
        rel_props = ' {{{0}}}'.format(', '.join(
            ['{}: {}'.format(key, value) for key, value in relation_properties.items()]))

    # direct, relation_type=None is unspecified, relation_type
    if relation_type is None:
        stmt = stmt.format('')
    # all("*" wildcard) relation_type
    elif relation_type == '*':
        stmt = stmt.format('[*]')
    else:
        # explicit relation_type
        stmt = stmt.format('[%s:`%s`%s]' % (ident if ident else '', relation_type, rel_props))

    return "({0}){1}({2})".format(lhs, stmt, rhs)


# special operators
_SPECIAL_OPERATOR_IN = 'IN'
_SPECIAL_OPERATOR_INSENSITIVE = '(?ui)'
_SPECIAL_OPERATOR_ISNULL = 'IS NULL'
_SPECIAL_OPERATOR_ISNOTNULL = 'IS NOT NULL'
_SPECIAL_OPERATOR_REGEX = '=~'

_UNARY_OPERATORS = (_SPECIAL_OPERATOR_ISNULL, _SPECIAL_OPERATOR_ISNOTNULL)

_REGEX_INSESITIVE = _SPECIAL_OPERATOR_INSENSITIVE + '{}'
_REGEX_CONTAINS = '.*{}.*'
_REGEX_STARTSWITH = '{}.*'
_REGEX_ENDSWITH = '.*{}'

# regex operations that require escaping
_STRING_REGEX_OPERATOR_TABLE = {
    'iexact': _REGEX_INSESITIVE,
    'contains': _REGEX_CONTAINS,
    'icontains': _SPECIAL_OPERATOR_INSENSITIVE + _REGEX_CONTAINS,
    'startswith': _REGEX_STARTSWITH,
    'istartswith': _SPECIAL_OPERATOR_INSENSITIVE + _REGEX_STARTSWITH,
    'endswith': _REGEX_ENDSWITH,
    'iendswith': _SPECIAL_OPERATOR_INSENSITIVE + _REGEX_ENDSWITH,
}
# regex operations that do not require escaping
_REGEX_OPERATOR_TABLE = {
    'iregex': _REGEX_INSESITIVE,
}
# list all regex operations, these will require formatting of the value
_REGEX_OPERATOR_TABLE.update(_STRING_REGEX_OPERATOR_TABLE)

# list all supported operators
OPERATOR_TABLE = {
    'lt': '<',
    'gt': '>',
    'lte': '<=',
    'gte': '>=',
    'ne': '<>',
    'in': _SPECIAL_OPERATOR_IN,
    'isnull': _SPECIAL_OPERATOR_ISNULL,
    'regex': _SPECIAL_OPERATOR_REGEX,
    'exact': '='
}
# add all regex operators
OPERATOR_TABLE.update(_REGEX_OPERATOR_TABLE)


def install_traversals(cls, node_set):
    """
    For a StructuredNode class install Traversal objects for each
    relationship definition on a NodeSet instance
    """
    rels = cls.defined_properties(rels=True, aliases=False, properties=False)

    for key, value in rels.items():
        if hasattr(node_set, key):
            raise ValueError("Can't install traversal '{}' exists on NodeSet".format(key))

        rel = getattr(cls, key)
        rel._lookup_node_class()

        traversal = Traversal(source=node_set, name=key, definition=rel.definition)
        setattr(node_set, key, traversal)


re._alphanum_str = frozenset(
    "_абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ\
            abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890")


def preprocess_filter_find_args(cls, kwargs):
    # realisation
    key, value = list(kwargs.items())[0]

    if len(re.findall(r"__", key)) != 2:
        raise Exception("Not found the double \'__\'")

    cuttener = key.find("__")
    relationship_field = key[0:cuttener]
    key = key[cuttener + 2:]
    del cuttener
    if not hasattr(cls, relationship_field):
        raise Exception("field does not exists in source fucking idiot")

    rel_field = getattr(cls, relationship_field)
    kwargs = {key: value}
    res = process_filter_args(rel_field.definition['model'], kwargs)
    # returns where definition
    return res, rel_field


def process_filter_args(cls, kwargs):
    """
    loop through properties in filter parameters check they match class definition
    deflate them and convert into something easy to generate cypher from
    """

    output = {}

    for key, value in kwargs.items():
        if '__' in key:
            prop, operator = key.rsplit('__')
            operator = OPERATOR_TABLE[operator]
        else:
            prop = key
            operator = '='

        if prop not in cls.defined_properties(rels=False):
            raise ValueError("No such property {} on {}".format(prop, cls.__name__))

        property_obj = getattr(cls, prop)
        if isinstance(property_obj, AliasProperty):
            prop = property_obj.aliased_to()
            deflated_value = getattr(cls, prop).deflate(value)
        else:
            # handle special operators
            if operator == _SPECIAL_OPERATOR_IN:
                if not isinstance(value, tuple) and not isinstance(value, list):
                    raise ValueError(
                        'Value must be a tuple or list for IN operation {}={}'.format(key, value))
                deflated_value = [property_obj.deflate(v) for v in value]
            elif operator == _SPECIAL_OPERATOR_ISNULL:
                if not isinstance(value, bool):
                    raise ValueError('Value must be a bool for isnull operation on {}'.format(key))
                operator = 'IS NULL' if value else 'IS NOT NULL'
                deflated_value = None
            elif operator in _REGEX_OPERATOR_TABLE.values():
                deflated_value = property_obj.deflate(value)
                if not isinstance(deflated_value, basestring):
                    raise ValueError('Must be a string value for {}'.format(key))
                if operator in _STRING_REGEX_OPERATOR_TABLE.values():
                    deflated_value = re.escape(deflated_value)
                deflated_value = operator.format(deflated_value)
                operator = _SPECIAL_OPERATOR_REGEX
            else:
                deflated_value = property_obj.deflate(value)

        # map property to correct property name in the database
        db_property = cls.defined_properties(rels=False)[prop].db_property or prop

        output[db_property] = (operator, deflated_value)
    return output


def process_has_args(builder, kwargs, is_not):
    """
    loop through has parameters check they correspond to class rels defined
    """
    # nodeset
    cls = builder.source_class
    # all relationships from model
    rel_definitions = cls.defined_properties(properties=False, rels=True, aliases=False)

    for key, value in kwargs.items():
        if key not in rel_definitions:
            raise ValueError("No such relation {} defined on a {}".format(key, cls.__name__))

        # initialize lookup node class of `key` field from source_class
        rel_definitions[key]._lookup_node_class()
        # get `key` definition from field
        definition = rel_definitions[key].definition
        # set parameters for build class, for create relationship query
        update_matches(value, builder, key, definition, is_not, **kwargs)


def process_union(nodeset, kwargs):
    """
    Attrs:
        nodeset: source nodeset
        kwargs - params
    """
    assert len(kwargs.items()) > 1, 'Union function may union greater or equal than 2 nodesets'

    # source model
    cls = nodeset.source_class
    # all relationships from model
    rel_definitions = cls.defined_properties(properties=False, rels=True, aliases=False)

    for key, value in kwargs.items():
        if key not in rel_definitions:
            raise ValueError("No such relation {} defined on a {}".format(key, cls.__name__))
        # initialize lookup node class of `key` field from source_class
        rel_definitions[key]._lookup_node_class()
        # get `key` definition from field
        definition = rel_definitions[key].definition
        union_block = UnionBlock(nodeset, key, definition, kwargs[key], source_model=cls)
        nodeset.union_operations.append(union_block)


@singledispatch
def update_matches(argument, builder, key, definition, *args, **kwargs):
    raise NotImplementedError("Type {} not implemented yet".format(type(argument)))


@singledispatch
def generate_label(type, **kwargs):
    raise NotImplementedError(
        "Label of type: {}, not implemented yet.".format(
            type(
                kwargs['value'])))


@generate_label.register(StructuredNode)
def _generate_label_sn(type, **kwargs):
    self = kwargs['self']
    supplicant = kwargs['label'].lower()[1:] + "_uid"
    label = kwargs['label'] + ' {{ uid: ${}}}'.format(supplicant)
    where_relation = _rel_helper(
        lhs=kwargs['source_ident'],
        rhs=label,
        ident='',
        **kwargs['val'])
    self._ast['where'].append(where_relation)
    self._query_params.update({supplicant: kwargs['value'].uid})
    return self


@update_matches.register(bool)
def _um_register(argument, builder, key, definition, *args, **kwargs):
    """
    default match
    """
    if argument:
        builder.must_match[key] = {'definition': definition}
    else:
        builder.dont_match[key] = {'definition': definition}


class QueryBuilder(object):

    def __init__(self, node_set):
        self.node_set = node_set
        self._ast = {
            'match': [],
            'where': [],
            'lookup': [],
            'distinct': node_set.is_distinct,
        }
        if hasattr(node_set, 'lookup') and node_set.lookup is not None:
            self._ast['lookup'].append(node_set.lookup)
        self._query_params = {}
        if hasattr(node_set, '_query_params'):
            self._query_params = node_set._query_params
        self._place_holder_registry = {}
        self._ident_count = 0

        # no label in ident
        self.no_label_in_ident = False
        if hasattr(node_set, 'no_label_in_ident'):
            self.no_label_in_ident = node_set.no_label_in_ident

        # custom returnable fields
        self._return_fields = []
        if hasattr(node_set, '_return_fields'):
            self._return_fields = ", ".join(node_set._return_fields)

    def build_ast(self):
        self.build_source(self.node_set)

        if hasattr(self.node_set, '_extra_queries') and self.node_set._extra_queries['need']:
            self.build_extra_ast(self.node_set._extra_queries)

        if getattr(self.node_set, 'skip', None) is not None:
            self._ast['skip'] = self.node_set.skip
        if getattr(self.node_set, 'limit', None) is not None:
            self._ast['limit'] = self.node_set.limit
        return self

    def build_extra_ast(self, extra):
        quote = ','
        if extra['match']:
            self._ast['match'] = list(set(self._ast['match']) | set(extra['match']))
        if extra.get('return') is not None:
            self._ast['return'] = extra['return']

    def build_relationship_filters(self, ident, filters, source_class):
        if filters is not None and isinstance(filters, QBase):
            match_stmt, where_stmt = self._parse_q_find_filters(ident, filters, source_class, [])
            match_stmt = set(match_stmt)
            if match_stmt:
                self._ast['match'] += list(match_stmt)
            self._ast['where'].append(where_stmt)

    def _parse_q_find_filters(self, ident, q, source_class, matches=[]):
        target = []
        for child in q.children:
            if isinstance(child, QBase):
                matches, q_childs = self._parse_q_find_filters(ident, child, source_class, matches)
                if child.connector == Q.OR:
                    q_childs = "(" + q_childs + ")"
                target.append(q_childs)
            else:
                kwargs = {child[0]: child[1]}

                # match relationship filters
                filters, rel_field = preprocess_filter_find_args(source_class, kwargs)

                rhs_ident = get_rhs_ident(rel_field)
                rel_ident = rhs_ident.lower()

                matches.append(
                    _rel_helper(
                        ident,
                        ':' + rhs_ident,
                        rel_ident,
                        rel_field.definition['relation_type']))

                # relationship where statement
                for prop, op_and_val in filters.items():
                    op, val = op_and_val
                    if op in _UNARY_OPERATORS:
                        # unary operators do not have a parameter
                        statement = '{}.{} {}'.format(rel_ident, prop, op)
                    else:
                        place_holder = self._register_place_holder(rel_ident + '_' + prop)
                        statement = '{}.{} {} {{{}}}'.format(rel_ident, prop, op, place_holder)
                        self._query_params[place_holder] = val
                    target.append(statement)
        ret = ' {} '.format(q.connector).join(target)
        if q.negated:
            ret = 'NOT ({})'.format(ret)
        return matches, ret

    def build_source(self, source):
        if isinstance(source, (Traversal, TraversalRelationships)):
            return self.build_traversal(source)
        elif isinstance(source, NodeSet):

            if inspect.isclass(source.source) and issubclass(source.source, StructuredNode):
                attrs = (source.ident, source)
                self.generate_union_query(*attrs)
                ident = self.build_label(*attrs)
            else:
                ident = self.build_source(source.source)

            # has, union calls
            self.build_additional_match(ident, source)

            # order by filtering
            if hasattr(source, '_order_by'):
                self.build_order_by(ident, source)

            # base filtering
            if source.filters or source.q_filters:
                self.build_where_stmt(
                    ident,
                    source.filters,
                    source.q_filters,
                    source_class=source.source_class)

            if source.relationship_filters:
                self.build_relationship_filters(ident,
                                                source.relationship_filters,
                                                source.source_class)

            return ident
        elif isinstance(source, StructuredNode):
            return self.build_node(source)
        else:
            raise ValueError("Unknown source type " + repr(source))

    def create_ident(self):
        self._ident_count += 1
        return 'r' + str(self._ident_count)

    def build_order_by(self, ident, source):
        if '?' in source._order_by:
            self._ast['with'] = '{}, rand() as r'.format(ident)
            self._ast['order_by'] = 'r'
        else:
            self._ast['order_by'] = []
            for p in source._order_by:
                if p['ident']:
                    self._ast['order_by'].append('{}.{}'.format(ident, p['name'] + p['type']))
                else:
                    self._ast['order_by'].append('{}'.format(p['name'] + p['type']))

    def build_traversal(self, traversal):
        """
        traverse a relationship from a node to a set of nodes
        """
        # build source
        lhs_ident = self.build_source(traversal.source)
        rhs_ident = "{alias}:{def_label}".format(
            alias=traversal.name,
            def_label=traversal.definition.get('node_class').__label__)

        if isinstance(traversal, TraversalRelationships):
            self._ast['return'] = traversal.rel_ident
            rel_ident = traversal.rel_ident
        elif isinstance(traversal, Traversal):
            self._ast['return'] = traversal.name
            rel_ident = self.create_ident()

        self._ast['result_class'] = traversal.target_class

        # create static rel ident
        stmt = _rel_helper(lhs=lhs_ident, rhs=rhs_ident, ident=rel_ident, **traversal.definition)
        self._ast['match'].append(stmt)

        if traversal.filters:
            self.build_where_stmt(rel_ident, traversal.filters)
        return traversal.name

    def build_node(self, node):
        ident = node.__class__.__name__.lower()
        place_holder = self._register_place_holder(ident)

        # Hack to emulate START to lookup a node by id
        _node_lookup = 'MATCH ({}) WHERE id({})={{{}}} WITH {}'.format(
            ident, ident, place_holder, ident)
        self._ast['lookup'].append(_node_lookup)

        self._query_params[place_holder] = node.id

        self._ast['return'] = ident
        self._ast['result_class'] = node.__class__
        return ident

    def build_label(self, ident, nodeset):
        """
        match nodes by a label
        """
        ident_w_label = ident if self.no_label_in_ident else \
            ident + ':' + nodeset.source.__label__
        self._ast['match'].append('({})'.format(ident_w_label))
        if self._return_fields:
            self._ast['return'] = ident + ', ' + self._return_fields
        else:
            self._ast['return'] = ident
        self._ast['result_class'] = nodeset.source
        return ident

    def generate_query(self, ident, node_set, attr='must_match'):
        for key, value in getattr(node_set, attr).items():
            if isinstance(value, dict):
                val = value['definition']
                label = ':' + val['node_class'].__label__
                if attr == 'extra_match':
                    # insert value definitions in match
                    # value['value'] = nodeset in has keyword arguments
                    # key - keyword in has argument
                    # value - definition set
                    # label - Relationship key definition field label
                    # val - Relationship definition ('node_class', 'label', ...)
                    # node_set - self nodeset
                    props = {
                        'val': val,
                        'source_ident': ident,
                        'label': label,
                        'value': value['value'],
                        'nodeset_origin': node_set,
                        'self': self,
                        'is_not': value.get('is_not', False),
                    }
                    generate_label(value['value'], **props)
                else:
                    where_rel = _rel_helper(lhs=ident, rhs=label, ident='', **val)
                    stmt = [where_rel, ] if attr == 'must_match' else ['NOT ' + where_rel, ]
                    self._ast['where'] += stmt
            else:
                raise ValueError("Expecting dict got: " + repr(value))

    def generate_union_query(self, ident, nodeset):
        if nodeset.union_operations:
            self.no_label_in_ident = True

            query, params = UnionBlock.union_queries(ident, nodeset.union_operations)

            # extend cypher realisation
            self._ast['lookup'].append(query)
            self._query_params.update(params)

    def build_additional_match(self, ident, node_set):
        """handle additional matches supplied by 'has()' calls
        """
        self.generate_query(ident, node_set)
        self.generate_query(ident, node_set, 'dont_match')
        self.generate_query(ident, node_set, 'extra_match')

    def _register_place_holder(self, key):
        self._place_holder_registry[key] = self._place_holder_registry.get(key, 0) + 1
        return key + '_' + str(self._place_holder_registry[key])

    def _where_query(self, ident, q, source_class):
        match_stmt, where_stmt = self._parse_q_filters(ident, q, source_class)
        match_stmt = set(match_stmt)
        if match_stmt:
            self._ast['match'] += list(match_stmt)
        if where_stmt:
            self._ast['where'].append(where_stmt)
        return where_stmt, self._query_params

    def _parse_q_filters(self, ident, q, source_class, matches=[]):
        target = []
        for child in q.children:
            if isinstance(child, QBase):
                matches, q_childs = self._parse_q_filters(ident, child, source_class, matches)
                if child.connector == Q.OR:
                    q_childs = "(" + q_childs + ")"
                target.append(q_childs) if q_childs else None
            else:
                # for identation of relationship fields or not
                operator = ident
                kwargs = {child[0]: child[1]}
                if len(child[0].split('__')) == 3:
                    # find_by_edge
                    filters, rel_field = preprocess_filter_find_args(source_class, kwargs)

                    rhs_ident = get_rhs_ident(rel_field)
                    rel_ident = "{}_{}".format(ident, rhs_ident.lower())

                    matches.append(
                        _rel_helper(
                            ident,
                            ':' + rhs_ident,
                            rel_ident,
                            rel_field.definition['relation_type']))
                    operator = rel_ident
                else:
                    # base filter
                    filters = process_filter_args(source_class, kwargs)
                for prop, op_and_val in filters.items():
                    op, val = op_and_val
                    if op in _UNARY_OPERATORS:
                        # unary operators do not have a parameter
                        statement = '{}.{} {}'.format(operator, prop, op)
                    else:
                        place_holder = self._register_place_holder(operator + '_' + prop)
                        statement = '{}.{} {} {{{}}}'.format(operator, prop, op, place_holder)
                        self._query_params[place_holder] = val
                    target.append(statement)
        ret = ' {} '.format(q.connector).join(target)
        if q.negated:
            ret = 'NOT ({})'.format(ret)
        return matches, ret

    def build_where_stmt(self, ident, filters, q_filters=None, source_class=None):
        """
        construct a where statement from some filters
        """
        if q_filters is not None:
            match_stmt, where_stmt = self._parse_q_filters(ident, q_filters, source_class, [])
            match_stmt = set(match_stmt)
            if match_stmt:
                self._ast['match'] += list(match_stmt)
            if where_stmt:
                self._ast['where'].append(where_stmt)
        else:
            # DEPRECATED
            stmts = []
            for row in filters:
                negate = False

                # pre-process NOT cases as they are nested dicts
                if '__NOT__' in row and len(row) == 1:
                    negate = True
                    row = row['__NOT__']

                for prop, op_and_val in row.items():
                    op, val = op_and_val
                    if op in _UNARY_OPERATORS:
                        # unary operators do not have a parameter
                        statement = '{} {}.{} {}'.format('NOT' if negate else '', ident, prop, op)
                    else:
                        place_holder = self._register_place_holder(ident + '_' + prop)
                        statement = '{} {}.{} {} {{{}}}'.format(
                            'NOT' if negate else '', ident, prop, op, place_holder)
                        self._query_params[place_holder] = val
                    stmts.append(statement)

            self._ast['where'].append(' AND '.join(stmts))

    def build_query(self, return_operation='RETURN'):
        query = self.build_query_build_lookup()
        query = self.build_query_build_match(query)
        query = self.build_query_build_where(query)
        query = self.build_query_build_with(query)
        query = self.build_query_build_return(query, return_operation)
        return query

    @classmethod
    def gather_lookup_identations(cls, lookups):
        identations = ""
        for indice, lookup_query in enumerate(lookups):
            if indice > 0:
                # append after first lookup query
                lookup_query += identations
            identations += ', ' + lookup_query.strip(' ').split(" ")[-1]
            yield lookup_query

    def build_query_build_lookup(self, query=''):
        if 'lookup' in self._ast and self._ast['lookup']:
            lookups = filter(lambda lookup: lookup, self._ast['lookup'])
            lookups = self.gather_lookup_identations(lookups)
            query += "\n".join(lookups)
        return query

    def build_query_build_match(self, query=''):
        query += ' MATCH '
        query += ', '.join(['({})'.format(i) for i in self._ast['match']])
        return query

    def build_query_build_where(self, query='', **params):
        if 'where' in self._ast and self._ast['where']:
            if params.get('where', True):
                query += ' WHERE '
            query += ' AND '.join(self._ast['where'])
        return query

    def build_query_build_with(self, query=''):
        if 'with' in self._ast and self._ast['with']:
            query += ' WITH '
            query += self._ast['with']
        return query

    def build_query_build_return(self, query='', return_operation=None):
        # set WITH or RETURN value
        query += ' {} '.format(return_operation)
        # set DISTINCT or ''
        if self._ast['distinct']:
            query += "DISTINCT "
        # return ident
        query += self._ast['return']
        if 'order_by' in self._ast and self._ast['order_by']:
            query += ' ORDER BY '
            query += ', '.join(self._ast['order_by'])
        if 'skip' in self._ast:
            query += ' SKIP {0:d}'.format(self._ast['skip'])
        if 'limit' in self._ast:
            query += ' LIMIT {0:d}'.format(self._ast['limit'])
        return query

    def _count(self):
        return_operation = "RETURN"
        return_string = self._ast['return']
        if ',' in return_string:
            return_operation = 'WITH'
            self._ast['return'] = return_string + " RETURN count(*)"
        else:
            self._ast['return'] = 'count({})'.format(return_string)

        #  self._ast['return'] = 'count({})'.format("*")

        # drop order_by, results in an invalid query
        self._ast.pop('order_by', None)
        # drop limit & offset
        limit = self._ast.pop('limit', None)
        skip = self._ast.pop('skip', None)
        query = self.build_query(return_operation)
        results, _ = db.cypher_query(query, self._query_params)
        # update it
        self._ast['limit'] = limit
        self._ast['skip'] = skip
        return int(results[0][0])

    def _contains(self, node_id):
        # inject id = into ast
        ident = self._ast['return']
        place_holder = self._register_place_holder(ident + '_contains')
        self._ast['where'].append('id({}) = {{{}}}'.format(ident, place_holder))
        self._query_params[place_holder] = node_id
        return self._count() >= 1

    def _execute(self):
        query = self.build_query()
        results, _ = db.cypher_query(query, self._query_params, resolve_objects=True)
        # The following is not as elegant as it could be but had to be copied from the
        # version prior to cypher_query with the resolve_objects capability.
        # It seems that certain calls are only supposed to be focusing to the first
        # result item returned (?)
        if results:
            return [n[0] for n in results]
        return []

    def _execute_multiple(self, resolve_objects=True):
        query = self.build_query()
        results, _ = db.cypher_query(query,
                                     self._query_params,
                                     resolve_objects=resolve_objects)
        if results:
            return results
        return []


class BaseSet(object):
    """
    Base class for all node sets.

    Contains common python magic methods, __len__, __contains__ etc
    """
    query_cls = QueryBuilder

    def __init__(self, *args, **kwargs):
        self.is_distinct = False
        #  order_by = [
        #    {
        #      "field_name": "",
        #      "type": "-",
        #      "identation": True
        #    },
        #  ]
        self._order_by = []

    def distinct(self, f=True):
        """
        Return DISTINCT results
        """
        self.is_distinct = f
        return self

    def all(self, multiple=False):
        """
        Return all nodes belonging to the set
        :return: list of nodes
        :rtype: list
        """
        qb = self.query_cls(self).build_ast()
        if multiple:
            return qb._execute_multiple()
        return qb._execute()

    @property
    def ident(self):
        return self.source.__label__.lower()

    def __iter__(self):
        return (i for i in self.query_cls(self).build_ast()._execute())

    def __len__(self):
        return self.query_cls(self).build_ast()._count()

    def __bool__(self):
        return self.query_cls(self).build_ast()._count() > 0

    def __nonzero__(self):
        return self.query_cls(self).build_ast()._count() > 0

    def __contains__(self, obj):
        if isinstance(obj, StructuredNode):
            if hasattr(obj, 'id'):
                return self.query_cls(self).build_ast()._contains(int(obj.id))
            raise ValueError("Unsaved node: " + repr(obj))
        else:
            raise ValueError("Expecting StructuredNode instance")

    def __getitem__(self, key):
        if isinstance(key, slice):
            if key.stop and key.start:
                self.limit = key.stop - key.start
                self.skip = key.start
            elif key.stop:
                self.limit = key.stop
            elif key.start:
                self.skip = key.start

            return self.query_cls(self).build_ast()._execute()

        elif isinstance(key, int):
            self.skip = key
            self.limit = 1

            return self.query_cls(self).build_ast()._execute()[0]


class NodeSet(BaseSet):
    """
    A class representing as set of nodes matching common query parameters
    """

    def __init__(self, source, *args, **kwargs):
        super(NodeSet, self).__init__(*args, **kwargs)
        self.source = source  # could be a Traverse object or a node class
        if isinstance(source, Traversal):
            self.source_class = source.target_class
        elif inspect.isclass(source) and issubclass(source, StructuredNode):
            self.source_class = source
        elif isinstance(source, StructuredNode):
            self.source_class = source.__class__
        else:
            raise ValueError("Bad source for nodeset " + repr(source))

        # setup Traversal objects using relationship definitions
        install_traversals(self.source_class, self)
        self.implicit_init()

    def implicit_init(self,):
        # lazy query builds
        # Fields: return, match, where
        self._extra_queries = {
            'match': [],
            'where': [],
            'need': False,
        }
        # filters
        self.filters = []
        self.q_filters = Q()
        self.relationship_filters = Q()

        # used by has()
        self.must_match = {}
        self.dont_match = {}
        # for nodeset or structured node relationships
        self.extra_match = {}

        # for extend cypher query (from query, to NodeSet)
        self.no_label_in_ident = False
        self._query_params = {}
        self.lookup = ""

        # for union operations (List[Type[UnionBlock]])
        self.union_operations = []

        # for return aliases
        self._return_fields = list()

    def extend_cypher(self, query, params={}):
        """
        Attrs:
            query (str): Cypher query.
            params (dict): Cypher params in `query` param
            query_ident: ident that placed in `query` after WITH predicate
        Returns:
            <NodeSet>
        """
        assert isinstance(query, str), 'query should be str instance'
        assert isinstance(params, dict), 'params should be dict instance'

        self.no_label_in_ident = True

        assert 'WITH' in query, 'query should contains and returns WITH statement'

        query += " AS {}".format(self.ident)

        self.lookup += query
        self._query_params.update(params)
        return self

    def return_fields(self, fields):
        assert isinstance(fields, list), "Fields should be the list type"
        self._return_fields += fields
        return self

    def to_q_filters(self, filters):
        return reduce(lambda initial, n: initial & Q(**{n[0]: n[1]}),
                      filters.items(), Q())

    def set_limit(self, limit=200):
        """
        Lazy limit
        Attrs:
            limit: int (default=200): if None provided, default used
        Returns:
            <NodeSet>
        """
        self.limit = limit
        return self

    def set_skip(self, skip=0):
        """
        Lazy skip
        Attrs:
            skip: int (default=0): if None provided, default used
        Returns:
        """
        self.skip = skip
        return self

    @property
    def query_builder(self, ):
        """
        Return <QueryBuilder> instance.
        """
        return self.query_cls(self).build_ast()

    def _get(self, limit=None, **kwargs):
        self.filter(**kwargs)
        if limit:
            self.limit = limit
        return self.query_cls(self).build_ast()._execute()

    def get(self, **kwargs):
        """
        Retrieve one node from the set matching supplied parameters

        :param kwargs: same syntax as `filter()`
        :return: node
        """
        result = self._get(limit=2, **kwargs)
        if len(result) > 1:
            raise MultipleNodesReturned(repr(kwargs))
        elif not result:
            raise self.source_class.DoesNotExist(repr(kwargs))
        else:
            return result[0]

    def get_or_none(self, **kwargs):
        """
        Retrieve a node from the set matching supplied parameters or return none

        :param kwargs: same syntax as `filter()`
        :return: node or none
        """
        try:
            return self.get(**kwargs)
        except self.source_class.DoesNotExist:
            pass

    def first(self, **kwargs):
        """
        Retrieve the first node from the set matching supplied parameters

        :param kwargs: same syntax as `filter()`
        :return: node
        """
        result = result = self._get(limit=1, **kwargs)
        if result:
            return result[0]
        else:
            raise self.source_class.DoesNotExist(repr(kwargs))

    def first_or_none(self, **kwargs):
        """
        Retrieve the first node from the set matching supplied parameters or return none

        :param kwargs: same syntax as `filter()`
        :return: node or none
        """
        try:
            return self.first(**kwargs)
        except self.source_class.DoesNotExist:
            pass

    def filter(self, *args, **kwargs):
        """
        Apply filters to the existing nodes in the set.

        :param kwargs: filter parameters

            Filters mimic Django's syntax with the double '__' to separate field and operators.

            e.g `.filter(salary__gt=20000)` results in `salary > 20000`.

            The following operators are available:

             * 'lt': less than
             * 'gt': greater than
             * 'lte': less than or equal to
             * 'gte': greater than or equal to
             * 'ne': not equal to
             * 'in': matches one of list (or tuple)
             * 'isnull': is null
             * 'regex': matches supplied regex (neo4j regex format)
             * 'exact': exactly match string (just '=')
             * 'iexact': case insensitive match string
             * 'contains': contains string
             * 'icontains': case insensitive contains
             * 'startswith': string starts with
             * 'istartswith': case insensitive string starts with
             * 'endswith': string ends with
             * 'iendswith': case insensitive string ends with

        :return: self
        """
        if args or kwargs:
            self.q_filters = Q(self.q_filters & Q(*args, **kwargs))
        return self

    def exclude(self, *args, **kwargs):
        """
        Exclude nodes from the NodeSet via filters.

        :param kwargs: filter parameters see syntax for the filter method
        :return: self
        """
        if args or kwargs:
            self.q_filters = Q(self.q_filters & ~Q(*args, **kwargs))
        return self

    def has(self, **kwargs):
        """
        Nodeset, object, bool types
        Supports operation
         & or |
        """
        is_not = kwargs.pop('is_not', False)
        process_has_args(self, kwargs, is_not=is_not)
        return self

    def union(self, **kwargs):
        process_union(self, kwargs)
        return self

    def find_by_edge(self, *args, **kwargs):
        """
        Extra filter for find nodes by relationship properties
        Example:
            .find_by_edge(Q(research__ru_name__icontains='value'))

            research - relationship definition in our model of nodeset,
                or relationship field at simple
            ru_name - field on edge (defined in relationship_model=in relationship
                    definition)
            icontains - search param
        """
        if args or kwargs:
            self.q_filters = Q(self.q_filters & Q(*args, **kwargs))
        return self

    def append_relationship(self, field):
        """Field must be relationship definition
        Just append relationship to model. Without extra queries:
            Example:
                coffee.append_relationship('sugar') ->
            MATCH (coffee:Coffee), ((coffee)-[sugar:SugarCoffeeRel]-(sugar_node:Sugar)) ...
        """
        field = getattr(self.source_class, field, None)
        if field is None or not hasattr(field, 'definition'):
            raise NeomodelException("Field {} not found in source_class".format(field))
        if isinstance(self.source, Traversal):
            ident = self.source.target_class.__label__.lower()
        else:
            ident = self.ident
        rhs_ident = get_rhs_ident(field)
        rel_ident = "{}_{}".format(ident, rhs_ident.lower())
        rel_query = _rel_helper(ident,
                                ":{label}".format(label=rhs_ident),
                                rel_ident,
                                field.definition['relation_type'])
        extra_match_set = set(self._extra_queries['match'])
        extra_match_set |= set((rel_query, ))
        self._extra_queries['match'] = list(extra_match_set)
        self._extra_queries['need'] = True
        return self

    def set_return(self, string):
        """
        Warning: request may be incorrect
        """
        self._extra_queries['return'] = string
        self._extra_queries['need'] = True
        return self

    def order_by(self, *props, **kwargs):
        """
        Order by properties. Prepend with minus to do descending. Pass None to
        remove ordering.
        """
        pass_validation = kwargs.get('pass_validation', False)
        should_remove = len(props) == 1 and props[0] is None
        if not hasattr(self, '_order_by') or should_remove:
            self._order_by = []
            if should_remove:
                return self
        if '?' in props:
            self._order_by.append('?')
        else:
            for prop in props:
                prop = prop.strip()
                if prop.startswith('-'):
                    prop = prop[1:]
                    desc = True
                else:
                    desc = False

                if not pass_validation and prop not in self.source_class.defined_properties(
                        rels=False):
                    raise ValueError("No such property {} on {}".format(
                        prop, self.source_class.__name__))

                if not pass_validation:
                    property_obj = getattr(self.source_class, prop)
                    if isinstance(property_obj, AliasProperty):
                        prop = property_obj.aliased_to()
                self._order_by.append({
                    "name": prop,
                    "type": ' DESC ' if desc else '',
                    "ident": not pass_validation,
                })
        return self


@update_matches.register(NodeSet)
@update_matches.register(StructuredNode)
def _um_register(argument, builder, key, definition, *args, **kwargs):
    not_condition = {}
    if isinstance(argument, NodeSet) and args:
        not_condition = {'is_not': args[0]}
    builder.extra_match[key] = {
        'definition': definition,
        'type': type(argument),
        'value': argument,
        'operation': ' OR ' if 'operation' in kwargs and kwargs['operation'] == '|' else ' AND ',
        **not_condition,
    }


@generate_label.register(tuple)
@generate_label.register(list)
def _generate_label_iterable_collections(type, **kwargs):
    self = kwargs['self']
    # if in `has()` argument type is list or tuple
    labels = (kwargs['label'] + ' { uid: "' + value.uid + '"}' for value in kwargs['value'])
    where_relation = [_rel_helper(
        lhs=kwargs['source_ident'],
        rhs=label,
        ident='',
        **kwargs['val']) for label in labels]

    # add where
    self._ast['where'] += [where_relation, ]
    return self


@generate_label.register(NodeSet)
def _generate_label_ns(type, **kwargs):
    # if in `has()` argument type is `NodeSet`
    # query_params, build_query()
    is_not_linked = kwargs['is_not']
    self = kwargs['self']

    # NodeSet query builder (in `has` function)
    inner_query_builder = type.query_builder
    inner_ident = inner_query_builder.node_set.ident
    inner_query = inner_query_builder.build_query('WITH')

    # insert into origin nodeset `match` option
    matches = inner_query_builder._ast.setdefault('match', [])

    match_relation = _rel_helper(
        rhs=kwargs['source_ident'],
        lhs=inner_ident,
        reverse=True,
        **kwargs['val'])

    # add lookup
    self._ast.setdefault('lookup', [])
    self._ast['lookup'].append(inner_query)

    # add connection
    if not is_not_linked:
        self._ast['match'].append(match_relation)
    else:
        self._ast['where'].append("NOT EXISTS (" + match_relation + ")")

    # union query params of two nodesets
    self._query_params.update(inner_query_builder._query_params)
    return self


class Traversal(BaseSet):
    """
    Models a traversal from a node to another.

    :param source: Starting of the traversal.
    :type source: A :class:`~neomodel.core.StructuredNode` subclass, an
                  instance of such, a :class:`~neomodel.match.NodeSet` instance
                  or a :class:`~neomodel.match.Traversal` instance.
    :param name: A name for the traversal.
    :type name: :class:`str`
    :param definition: A relationship definition that most certainly deserves
                       a documentation here.
    :type defintion: :class:`dict`
    """

    def __init__(self, source, name, definition):
        """
        Create a traversal

        """
        super(Traversal, self).__init__()
        self.source = source

        if isinstance(source, Traversal):
            self.source_class = source.target_class
        elif inspect.isclass(source) and issubclass(source, StructuredNode):
            self.source_class = source
        elif isinstance(source, StructuredNode):
            self.source_class = source.__class__
        elif isinstance(source, NodeSet):
            self.source_class = source.source_class
        else:
            raise TypeError("Bad source for traversal: "
                            "{}".format(type(source)))

        invalid_keys = (
            set(definition) - {'direction', 'model', 'node_class', 'relation_type'}
        )
        if invalid_keys:
            raise ValueError(
                'Unallowed keys in Traversal definition: {invalid_keys}'
                .format(invalid_keys=invalid_keys)
            )

        self.definition = definition
        self.target_class = definition['node_class']
        self.name = name
        self.rel_ident = name
        self.filters = []
        self.no_label_in_ident = False

    def match(self, **kwargs):
        """
        Traverse relationships with properties matching the given parameters.

            e.g: `.match(price__lt=10)`

        :param kwargs: see `NodeSet.filter()` for syntax
        :return: self
        """
        if kwargs:
            if self.definition.get('model') is None:
                raise ValueError("match() with filter only available on relationships with a model")
            output = process_filter_args(self.definition['model'], kwargs)
            if output:
                self.filters.append(output)
        return self


class TraversalRelationships(Traversal):

    REL_IDENT = "static_ident_rel"

    def __init__(self, source, name, definition):
        super().__init__(source, name, definition)
        self.rel_ident = self.REL_IDENT
        self.name = name
        self.target_class = definition['model']
