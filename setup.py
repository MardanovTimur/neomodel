import sys

from setuptools import setup, find_packages

setup(
    name='neomodel-next',
    version='3.4.1',
    description='An object mapper for the neo4j graph database.',
    long_description=open('README.rst').read(),
    author='Robin Edwards; Mardanov Timur',
    author_email='timurmardanov97@gmail.com',
    zip_safe=True,
    url='http://github.com/MardanovTimur/neomodel-next',
    license='MIT',
    packages=find_packages(exclude=('tests',)),
    keywords='graph neo4j neomodel ORM OGM',
    scripts=['scripts/neomodel_install_labels', 'scripts/neomodel_remove_labels'],
    setup_requires=['pytest-runner'] if any(x in ('pytest', 'test') for x in sys.argv) else [],
    tests_require=['pytest'],
    install_requires=[
        'neo4j-driver>=1.5.2, <1.7.0',
        'pytz>=2016.10',
        'python-dateutil==2.8.0',
        'numpy>=1.12.1',
    ],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        'Intended Audience :: Developers',
        'Operating System :: OS Independent',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python',
        'Topic :: Software Development :: Libraries :: Python Modules',
        "Programming Language :: Python :: 3.6",
        "Topic :: Database",
    ])
