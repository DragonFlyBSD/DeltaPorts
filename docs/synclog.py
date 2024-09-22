#! /usr/bin/env python3
#
# Simple script to handle synclog
#
from importlib.util import find_spec
import argparse
import json
import sys


try:
    find_spec('tabulate')
    from tabulate import tabulate
except ImportError:
    print('Please install the tabulate module')
    sys.exit(255)

def parse_cmdline_args():
    parser = argparse.ArgumentParser(description='Synclog handling')

    parser.add_argument('-r',
                        dest='release',
                        action="store",
                        type=str,
                        help='Specify a release'
    )

    parser.add_argument('--todo-show',
                        action='store_true',
                        help='Show To-Do List for a specific release'
    )


    return parser.parse_args()

def load_synclog():
    with open('./synclog.json') as f:
        data = json.load(f)
    return data

def todolist_show(data):
    details = list(
        filter(
            lambda r: r['id'] == args.release,
            data['releases']
        )
    )

    d = details[0]
    print(f"RELEASE: {args.release}\n")
    print(
        tabulate(
            d['to-do'], headers={ 'port': 'Port',
                                  'desc': 'Description',
                                  'status': 'Status'}
        )
    )

def main():
    data = load_synclog()

    if args.release == None:
        # Grab current release
        args.release = data['current']

    if args.todo_show:
        todolist_show(data)

if __name__ == '__main__':
    args = parse_cmdline_args()
    main()
