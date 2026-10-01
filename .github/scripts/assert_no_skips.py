#!/usr/bin/env python3
"""Fail if a pytest JUnit report contains skipped tests (or no tests at all).

A skip is how a test says "I couldn't reach Postgres/Redis/Docker". Locally that is
fine; in CI it means a service silently failed to start and the job is green while
checking nothing. Usage: assert_no_skips.py reports/backend.xml [more.xml ...]
"""
import sys
import xml.etree.ElementTree as ET


def main(paths: list[str]) -> int:
    total = skipped = 0
    for path in paths:
        for suite in ET.parse(path).getroot().iter("testsuite"):
            total += int(suite.get("tests", 0))
            skipped += int(suite.get("skipped", 0))
    print(f"{total} tests, {skipped} skipped")
    if total == 0:
        print("::error::no tests ran")
        return 1
    if skipped:
        print(f"::error::{skipped} test(s) were skipped; a CI service is probably not reachable")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
