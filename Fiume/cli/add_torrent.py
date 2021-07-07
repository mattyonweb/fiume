from typing import *
import argparse
import pathlib
import json
import Fiume.config as config

parser = argparse.ArgumentParser(
    description="Adds a torrent to Fiume dowloading.json.",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter
)

parser.add_argument("torrent_path",
                    action="store",
                    type=str,
                    help="path to .torrent file")

parser.add_argument("output_path",
                    action="store",
                    type=str,
                    help="where to put downloaded files")

parser.add_argument("-f",
                    type=pathlib.Path,
                    default=config.IN_DOWNLOAD_FILE,
                    action="store",
                    dest="downloading_json",
                    help="path to custom downloading.json file")

########################################

def add_torrent(options=None):
    """
    Adds a new torrent to the .json downloading file.
    """
    
    if options is None: # for tests only
        options = vars(parser.parse_args())

        
    with open(options["downloading_json"], "r") as f:
        s = f.read()
        if s.strip() == "":
            js = []
        else:
            js = json.loads(s)

            
    # helpers
    new_torrent_path = options["torrent_path"]
    new_output_path  = options["output_path"]

    
    # If the new torrent I'm adding is already in the json,
    # remove it from the json and replace it
    already_there = list()
    for i, d in enumerate(js):
        if d["torrent_path"] == new_torrent_path:
            already_there.append(i)
    for i in already_there[::-1]:
        del js[i]

        
    js.append({"torrent_path": new_torrent_path,
               "output_file": new_output_path})

    with open(options["downloading_json"], "w") as f:
        f.write(json.dumps(js, indent=2))
