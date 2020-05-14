#!/usr/bin/env python
from setuptools import setup


with open("README.md") as readme_file:
    readme = readme_file.read()

setup(
    name="clicktimepy",
    version="2.0",
    description="Python library that supports ClickTime REST v2",
    long_description=readme,
    author="Michael Ihde",
    author_email="mihde@spectric.com",
    url="https://github.com/spectriclabs/clicktimepy",
    py_modules=["clicktime"],
)
