import sys


def main(paths):
    for path in paths:
        with open(path) as handle:
            handle.read()


if __name__ == "__main__":
    main(sys.argv[1:])
