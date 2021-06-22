import pathlib
from Fiume.config import CLIENT_VERSION 
from setuptools import setup, find_packages

# The directory containing this file
HERE = pathlib.Path(__file__).parent

# The text of the README file
README = (HERE / "README.md").read_text()

# This call to setup() does all the work
setup(
    name="Fiume",
    version=".".join(CLIENT_VERSION.decode()),
    description="A Bittorrent client for single-file torrents.",
    long_description=README,
    long_description_content_type="text/markdown",
    url="https://github.com/mattyonweb/fiume",
    author="Matteo Cavada",
    author_email="cvd00@insicuri.net",
    license="GNU General Public License v3.0",
    classifiers=[
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Topic :: Internet",
        "Development Status :: 2 - Pre-Alpha",
        "Programming Language :: Python :: 3.9",
    ],
    packages=find_packages(),
    # package_dir={"": "Fiume"},
    install_requires=[
        "pathos", "requests", "bencode.py"
    ],
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "fiume=Fiume.cli:main",
        ]
    },
    python_requires='>=3.9',
)
