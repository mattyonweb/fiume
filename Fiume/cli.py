import argparse
import pathlib
import logging
import bencodepy

import Fiume.metainfo_decoder as md
import Fiume.state_machine as sm

##################################################

def main():
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

    t.main()
