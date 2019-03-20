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
    datetime_auto_update = DateTimeProperty(auto_update=True)


def test_auto_update():
    c = Car(name='asasdasdasd')
    print(c.uid)
    c.save()
    c.name = 'asdasdassssss'
    c.save()
    print(c.defined_properties(aliases=False, rels=False))


if __name__ == "__main__":
    config.DATABASE_URL = os.environ.get('NEO4J_BOLT_URL',
                                         'bolt://neo4j:neo4j_admin@localhost:7687')
    config.AUTO_INSTALL_LABELS = True
    install_all_labels()

    # RUN LOCAL TESTS
    test_auto_update()
