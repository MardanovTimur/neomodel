from neomodel import db, config
from neomodel import (
    StructuredNode,
    StringProperty,
    DateProperty,
    DateTimeProperty,
    install_all_labels,
)
import os


class Car(StructuredNode):
    name = StringProperty()

    date = DateProperty()
    date_auto_update = DateProperty(auto_update=True)

    datetime = DateTimeProperty(default_now=True)
    datetime_auto_update = DateTimeProperty(default_now=True, auto_update=True)


def test_auto_update():
    c = Car(name='asasdasdasd')
    print(c.uid)
    c.save()
    print('First: ', c.date_auto_update)
    print('First_ datateim: ', c.datetime_auto_update)
    c.name = 'asdasdassssss'
    c.save()
    print('Second: ', c.date_auto_update)
    print('Second datetime: ', c.datetime_auto_update)


if __name__ == "__main__":
    config.DATABASE_URL = os.environ.get('NEO4J_BOLT_URL',
                                         'bolt://neo4j:neo4j_admin@localhost:7687')
    config.AUTO_INSTALL_LABELS = True
    install_all_labels()

    # RUN LOCAL TESTS
    test_auto_update()
