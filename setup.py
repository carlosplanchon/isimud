#!/usr/bin/env python3

import setuptools


with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="isimud",
    packages=["isimud"],
    version="0.1",
    license="GPL3",
    description="Package to get commonly used details of the network "
            "interface and access points you are using..",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Carlos A. Planchón",
    author_email="bubbledoloresuruguay2@gmail.com",
    url="https://github.com/carlosplanchon/isimud",
    download_url="https://github.com/carlosplanchon/"
        "isimud/archive/v0.1.tar.gz",
    keywords=["isimud", "sensors", "wifi", "networking"],
    classifiers=[
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Build Tools",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
    ],
)
