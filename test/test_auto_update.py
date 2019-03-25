from neomodel import db, config
from neomodel.match import NodeSet
from neomodel.cardinality import ZeroOrOne
from neomodel.relationship_manager import ZeroOrMore
from neomodel.relationship_manager import RelationshipFrom
from neomodel.relationship_manager import RelationshipTo
from neomodel import (
    StructuredNode,
    StringProperty,
    DateProperty,
    DateTimeProperty,
    install_all_labels,
)
import os
import logging

logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)


class User(StructuredNode):
    username = StringProperty()
    car = RelationshipFrom('Car', 'OWN', ZeroOrMore)


class Item(StructuredNode):
    car = RelationshipFrom('Car', 'U', ZeroOrMore)

class Car(StructuredNode):
    name = StringProperty()
    owner = RelationshipTo('User', 'OWN', ZeroOrOne)
    item = RelationshipTo('Item', 'U', ZeroOrMore)

    datetime_auto_update = DateTimeProperty(default_now=True, auto_update=True)


def delete(model):
    db.cypher_query("""
    MATCH (m:{model})
    DETACH DELETE m;
    """.format(model=model.__name__))


def test_has_functionality():
    delete(Car)
    delete(User)
    delete(Item)
    m = Car(name='maserrati').save()
    l = Car(name='lamborgini').save()
    h = Car(name='hyndau').save()

    item = Item().save()
    m.item.connect(item)
    m.item.connect(Item().save())

    u1 = User(username="Jack").save()
    u1.car.connect(m)

    u2 = User(username="Sasha").save()
    u2.car.connect(h)
    u2.car.connect(l)

    users = User.nodes.filter(username__in=["Jack", 'Sasha'])
    cars = Car.nodes.has(owner=users)
    cars = cars.has(item=item)
    print(cars.all())
    #  print(NodeSet(User.nodes.filter(username__in=['Jack', 'Sasha']).car).filter().all())

if __name__ == "__main__":
    config.DATABASE_URL = os.environ.get('NEO4J_BOLT_URL',
                                         'bolt://neo4j:neo4j_admin@localhost:7687')
    config.AUTO_INSTALL_LABELS = True
    install_all_labels()

    # RUN LOCAL TESTS
    test_has_functionality()
