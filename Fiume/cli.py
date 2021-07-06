import argparse
import pathlib
import logging
import bencodepy
import signal

import Fiume.metainfo_decoder as md
import Fiume.state_machine as sm
import Fiume.fiume as fm
import Fiume.utils as utils

##################################################

def main_single():
    options = sm.parser()
    
    with open(options["torrent_path"], "rb") as f:
        metainfo = md.MetaInfo(
            bencodepy.decode(f.read()) | options
        )

    tm = md.TrackerManager(metainfo, options)

    t = sm.ThreadedServer(
        metainfo, tm, 
        **options
    )

    
def main_multiple():
    options = sm.parser()

    app = fm.Fiume(options)
    app.begin_session()

    
def add_torrent():
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
                        # dest="torrent_path",
                        # type=pathlib.Path,
                        type=str,
                        help="path to .torrent file")

    parser.add_argument("output_path",
                        action="store",
                        # dest="output_path",
                        # type=pathlib.Path,
                        type=str,
                        help="where to put downloaded files")

    parser.add_argument("-f",
                        type=pathlib.Path,
                        default=config.IN_DOWNLOAD_FILE,
                        action="store",
                        dest="downloading_json",
                        help="path to custom downloading.json file")

    options = vars(parser.parse_args())

    with open(options["downloading_json"], "r") as f:
        js = json.load(f)

    torrent_path = options["torrent_path"]
    output_path  = options["output_path"]

    # If the new torrent I'm adding is already in the json,
    # remove it from the json and replace it
    already_there = list()
    for i, d in enumerate(js):
        if torrent_path in d.keys():
            already_there.append(i)
    for i in already_there[::-1]:
        del js[i]

    js.append({torrent_path : output_path})

    with open(options["downloading_json"], "w") as f:
        f.write(json.dumps(js))

    
