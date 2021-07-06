import pathlib
from Fiume.config import CLIENT_VERSION 

from setuptools import setup, find_packages
from setuptools.command.develop import develop
from setuptools.command.install import install


class PostDevelopCommand(develop):
    """Post-installation for development mode."""
    def run(self):
        develop.run(self)
        import Fiume.config #creates directories 

class PostInstallCommand(install):
    """Post-installation for installation mode."""
    def run(self):
        install.run(self)
        import Fiume.config

##########################################à

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
    cmdclass={ #specifica azioni da intraprendere post-installazione
        'develop': PostDevelopCommand,
        'install': PostInstallCommand,
    },
    entry_points={
        "console_scripts": [
            "fiume-single=Fiume.cli:main_single",
            "fiume=Fiume.cli:main_multiple",
            "fiume-add=Fiume.cli:add_torrent"
        ]
    },
    python_requires='>=3.9',
)
