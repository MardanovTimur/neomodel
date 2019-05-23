import os
import logging

from neomodel import db, config
from neomodel.match_q import Q
from neomodel.match import NodeSet
from neomodel.cardinality import ZeroOrOne
from neomodel.relationship_manager import ZeroOrMore
from neomodel.relationship_manager import RelationshipFrom
from neomodel.relationship_manager import RelationshipTo
from neomodel.relationship import StructuredRel
from neomodel import (
    StructuredNode,
    StringProperty,
    DateProperty,
    DateTimeProperty,
    install_all_labels,
)

logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)


class ItemCarRel(StructuredRel):
    name = StringProperty()


class User(StructuredNode):
    username = StringProperty()
    car = RelationshipFrom('Car', 'OWN', ZeroOrMore)


class Item(StructuredNode):
    car = RelationshipFrom('Car', 'U', ZeroOrMore, model=ItemCarRel)

class Car(StructuredNode):
    name = StringProperty()
    owner = RelationshipTo('User', 'OWN', ZeroOrOne)
    item = RelationshipTo('Item', 'U', ZeroOrMore, model=ItemCarRel)

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
    items = Item.nodes.filter()
    cars = cars.has(item=items)
    print(cars.all())

    user = User.nodes.first()

    user_cars = user.car.filter()
    user_cars = user_cars.has(item=Item.nodes.filter())
    print(list(user_cars))

    users = User.nodes.filter()
    cars = Car.nodes.has(item=items, is_not=True)
    cars = cars.has(owner=users, is_not=True).distinct()
    print(list(cars))


    user.car.connect_nodeset(Car.nodes.filter(Q(name='assdasdsaddasdasda')), inflate_rels=False)

    #  print(list(users))

    #  print(len(Car.nodes.filter(Q(item__name__icontains='fire') | Q(item__name__contains='asdsad') | Q(name__icontains='maser')).distinct().return_fields(['car_item', ])))
    raise Exception("stop")


    #  print(Car.nodes.filter(Q()).all())

    #  print(Car.nodes.has(owner=u2).all())


    query = """
    match (c:Car)
        WHERE c.uid = $car_uid
    WITH c
    """
    params = {'car_uid': '2'}
    cars = Car.nodes.extend_cypher(query, params).filter(name__icontains='asdasd').all()


    query = """
    CALL apoc.cypher.run("
        MATCH (i:Item)
            WHERE i.name = $swag
        RETURN i
    ", $params) YIELD value
    WITH value.i as item
    RETURN item;
    """
    results, _ = db.cypher_query(query, {'params': {'swag': "asdasd"}})
    print(results)

    cars_union = Car.nodes.union(item=Item.nodes.filter(car__name__exact='asdasd'), \
            owner=User.nodes.has(car=Car.nodes.filter())).filter(name__icontains='asdasd')
    print(cars_union.all())

    #  car = Car.nodes.filter()
    #  car = car.return_fields(['car', ])
    #  print(len(car))
    #  print(len(car[0].owner.all_relationships()))
    #  print(len(car[1].item.filter_relationships(Item.nodes.first(),
    #      Q(Q(name__icontains='asdasd') | Q(name__icontains='asdasxc')))))

    #  print(ItemCarRel.defined_properties(rels=False))



if __name__ == "__main__":
    config.DATABASE_URL = os.environ.get('NEO4J_BOLT_URL',
                                         'bolt://neo4j:neo4j_admin@localhost:7687')
    config.AUTO_INSTALL_LABELS = True
    install_all_labels()

    # RUN LOCAL TESTS
    test_has_functionality()
#      db.cypher_query("""
#  MATCH (user:User) WITH user
#  MATCH (user)--(a:Car) return user as u_union
#  UNION
#  MATCH (user:User) WITH user
#  MATCH (user)--(b:Car) return user as u_union""")
