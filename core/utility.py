from collections import Counter
import copy
import csv
import ipaddress
import math
import os
import re
import subprocess
import sys
import tempfile
import traceback
import datetime
import xml.etree.ElementTree as ET
from threading import Lock

# define ANSI color escape sequences
# Taken from: http://www.lihaoyi.com/post/BuildyourownCommandLinewithANSIescapecodes.html
# and: http://www.topmudsites.com/forums/showthread.php?t=413
SANE = "\u001b[0m"
GREEN = "\u001b[32m"
BRIGHT_GREEN = "\u001b[32;1m"
RED = "\u001b[31m"
YELLOW = "\u001b[33m"
BRIGHT_BLUE = "\u001b[34;1m"
MAGENTA = "\u001b[35m"
BRIGHT_CYAN = "\u001b[36;1m"

# other ANSI escape sequences
CURSOR_PREV_LINE = "\033[1A\r"
CLEAR_UNTIL_EOL = "\033[K"

# regex to strip escape sequences
ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")

# mutex to prevent concurrent printing
PRINT_MUTEX = Lock()

# storing all parsed network expressions
PARSED_NETWORK_EXPRS = {}


def add_wildcard_ip(network: str, store_hosts: bool = True):
    """
    Parse an IP (v4 or v6) wildcard expression (with range, prefix or '*') using Nmap's "-sL" option.
    Pairs of lowest and highest addresses for each block of IPs in the given network
    are stored in PARSED_NETWORK_EXPRESSIONS.

    :param network: the network expression to parse
    :param store_hosts: whether to additionally store the hosts within the network
    :return: True if expression could be parsed, False otherwise
    """

    def get_nmap_xml_hosts():
        """ Call Nmap and return all XML host elements as ElementTree nodes"""
        nonlocal nmap_call, file
        devnull_fd = open(os.devnull)
        subprocess.call(nmap_call.split(" "), stdout=devnull_fd, stderr=subprocess.STDOUT)
        nm_xml_tree = ET.parse(file.name)
        nmaprun_elem = nm_xml_tree.getroot()
        devnull_fd.close()
        return nmaprun_elem.findall("host")

    # if network expression has already been parsed before
    if network in PARSED_NETWORK_EXPRS:
        if len(PARSED_NETWORK_EXPRS[network]) > 1:  # hosts are already stored
            return True
        if not store_hosts:
            return True

    hosts = []
    host_ranges = []
    prev_ip = None

    # call Nmap and parse its host output
    with tempfile.NamedTemporaryFile() as file:
        # first try to parse as IPv4 address
        nmap_call = "nmap -n -sL -oX %s %s" % (file.name, network)
        host_elems = get_nmap_xml_hosts()

        if not host_elems:  # nmap could not parse IPv4 network expression
            # try to parse as IPv6 network expression
            nmap_call += " -6"
            host_elems = get_nmap_xml_hosts()
            if not host_elems:
                return False

        for host_elem in host_elems:
            ip = host_elem.find("address").attrib["addr"]
            if not host_ranges:
                host_ranges.append([ip, ip])
            elif prev_ip is not None:
                # if network expression does not yield continuous block of IPs
                if ip_str_to_int(ip) != (ip_str_to_int(prev_ip) + 1):
                    host_ranges[-1][1] = prev_ip
                    host_ranges.append([ip, ip])

            if store_hosts:
                hosts.append(ip)
            prev_ip = ip

    if host_ranges:
        host_ranges[-1][1] = prev_ip  # close last IP range
    if store_hosts:
        PARSED_NETWORK_EXPRS[network] = (hosts, host_ranges)
    else:
        PARSED_NETWORK_EXPRS[network] = (host_ranges)
    return True


def get_ip_ranges(network: str):
    """Return all IP blocks within the network, each as pair of lowest and highest address"""
    add_wildcard_ip(network)
    return PARSED_NETWORK_EXPRS[network][1]


def is_valid_net_addr(network: str):
    """
    Return True if given network or address is a valid IP expression
    Valid are all network expressions Nmap can parse (e.g. CIDR or wildcard).
    """
    return add_wildcard_ip(network)


def extend_network_to_hosts(network: str):
    """Return all host addresses that are part of this network"""
    add_wildcard_ip(network)
    return PARSED_NETWORK_EXPRS[network][0]


def is_ipv4(ip: str):
    """Return True if given address is a valid IPv4 address, otherwise return False"""
    try:
        ipaddress.IPv4Address(ip)
    except ipaddress.AddressValueError:
        return False
    return True


def is_ipv6(ip: str):
    """Return True if given address is a valid IPv6 address, otherwise return False"""
    try:
        ipaddress.IPv6Address(ip)
    except ipaddress.AddressValueError:
        return False
    return True


def ip_str_to_int(ip: str):
    """Return the integer corresponding to the given string IP address"""
    if is_ipv4(ip):
        return int(ipaddress.IPv4Address(ip))
    return int(ipaddress.IPv6Address(ip))


def del_hosts_outside_net(hosts: dict, network: str):
    """
    Delete all the hosts from the given dict that are
    not a part of the given network expression.
    """

    network_ranges = get_ip_ranges(network)
    network_ranges_int = [(ip_str_to_int(low), ip_str_to_int(high))
                          for (low, high) in network_ranges]
    hosts_cpy = copy.deepcopy(hosts)  # because a dict cannot change size during iteration
    for ip, _ in hosts_cpy.items():
        try:
            ip_int = ip_str_to_int(ip)
        except ValueError:
            continue
        if not any(low <= ip_int <= high for (low, high) in network_ranges_int):
            del hosts[ip]


def print_exception_and_continue(exc: Exception):
    """Prints the given exception with a textual wrapper"""
    print("Original exception is: ", file=sys.stderr)
    print(''.join(traceback.format_exception(etype=type(exc), value=exc,
                                             tb=exc.__traceback__)), file=sys.stderr)
    print("===========================================================", file=sys.stderr)
    print("Continuing with scan ...", file=sys.stderr)


def get_current_timestamp():
    """Return current datetime in format YYYYmmDD_HHMMSS"""
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def clear_previous_line():
    """Delete the last recently printed line"""
    print(CURSOR_PREV_LINE, end="")
    print(CLEAR_UNTIL_EOL, end="")


def hide_cursor():
    print("\033[?25l", end="")


def show_cursor():
    print("\033[?25h", end="")



def parse_as_csv(text: str):
    parser = csv.reader([text], skipinitialspace=True)
    val = next(parser)
    return val


def add_to_config(config: dict, statement: str):
    """Add the information from the statement to the given config"""

    key, val = statement.split("=", maxsplit=1)

    # replace quotes in key
    key = key.replace("\"", "")
    key = key.replace("'", "")

    # strip surrounding whitespace
    key = key.strip()
    val = val.strip()

    # remove surrounding quotes from config value
    if ((val.startswith("\"") and val.endswith("\"") and val.count("\"") == 2) or 
        (val.startswith("'") and val.endswith("'") and val.count("'") == 2)):
        val = val[1:-1]

    config[key] = val


def parse_config(filepath: str, base_config: dict = {}):
    """
    Parse the config file stored at the given filepath.
    Overrides the values in base_config, if provided.
    """

    def next_line():
        """Return current line and Increase line index"""
        nonlocal i, lines
        if i < len(lines):
            i += 1
            return lines[i - 1]
        return None

    config = copy.deepcopy(base_config)
    with open(filepath) as file:
        cur_module = "core"  # default to core
        if cur_module not in config:
            config[cur_module] = {}

        comment_started = False
        lines, i = file.readlines(), 0
        line = next_line()
        cur_text = line

        while line is not None:
            line = line.strip()

            if comment_started:
                # search for end of block comment
                if "*/" in line:
                    comment_started = False
                    line = line[line.rfind("*/")+2:]
                    cur_text += line
                else:
                    line = next_line()
                    continue

            if "/*" in line:
                # handle start of block comment
                if "*/" in line:
                    cur_text = line[:line.find("/*")] + line[line.rfind("*/")+2:]
                else:
                    cur_text = line[:line.find("/*")]
                    line = next_line()
                    comment_started = True
                    continue
            else:
                cur_text = line

            # check for line comment
            comment_start = cur_text.find("//")
            if comment_start != -1:
                cur_text = line[:comment_start]

            if cur_text.startswith("["):  # start of module segment header
                cur_module = cur_text[1:cur_text.find("]")]
                if cur_module not in config:
                    config[cur_module] = {}
            elif not cur_text == "":
                add_to_config(config[cur_module], cur_text)

            line = next_line()
            cur_text = line

    return config


def printit(text: str = "", end: str = "\n", color=SANE):
    """A function allowing for thread safe printing in AVAIN."""

    acquire_print()
    print(color, end="")
    print(text, end=end)
    if color != SANE:
        print(SANE, end="")
    sys.stdout.flush()
    release_print()


def acquire_print(timeout=None):
    """ Acquire the PRINT_MUTEX mutex for printing """

    if not timeout:
        success = PRINT_MUTEX.acquire()
    else:
        success = PRINT_MUTEX.acquire(timeout=timeout)

    return success


def release_print():
    """ Release the PRINT_MUTEX mutex for printing """

    PRINT_MUTEX.release()


def compute_cosine_similarity(text_1: str, text_2: str, text_vector_regex=r"\w+"):
    """
    Compute the cosine similarity of two text strings.
    :param text_1: the first text
    :param text_2: the second text
    :return: the cosine similarity of the two text strings
    """

    def text_to_vector(text: str):
        """
        Get the vector representation of a text. It stores the word frequency
        of every word contained in the given text.
        :return: a Counter object that stores the word frequencies in a dict
                 with the respective word as key
        """
        word = re.compile(text_vector_regex)
        words = word.findall(text)
        return Counter(words)

    text_vector_1, text_vector_2 = text_to_vector(text_1), text_to_vector(text_2)

    intersecting_words = set(text_vector_1.keys()) & set(text_vector_2.keys())
    inner_product = sum([text_vector_1[w] * text_vector_2[w] for w in intersecting_words])

    abs_1 = math.sqrt(sum([cnt**2 for cnt in text_vector_1.values()]))
    abs_2 = math.sqrt(sum([cnt**2 for cnt in text_vector_2.values()]))
    normalization_factor = abs_1 * abs_2

    if not normalization_factor:  # avoid divison by 0
        return 0.0
    return float(inner_product)/float(normalization_factor)


def is_neq_prefix(text_1: str, text_2: str):
    """Return True if text_1 is a non-equal prefix of text_2, otherwise return False"""
    return text_1 != text_2 and text_2.startswith(text_1)


def neq_in(text_1: str, text_2: str):
    """Return True if text_1 is a non-equal part of text_2, otherwise return False"""
    return text_1 != text_2 and text_1 in text_2


def get_creation_date(filepath):
        """
        Get the creation date of a file on a Unix based system.
        If creation date is not available, return last-modified date.
        Taken and adapted from https://stackoverflow.com/a/39501288 .
        """
        filestat = os.stat(filepath)
        try:
            return datetime.datetime.fromtimestamp(filestat.st_birthtime)
        except AttributeError:
            return datetime.datetime.fromtimestamp(filestat.st_mtime)


def color_elements_in_string(line, regex, color):
    """
    Color the groups that the given regex produces when matching the given line.
    """

    match = re.match(regex, line)
    colored_line = line
    if match:
        replaced = set()
        for i in range (1, 6):
            group = match.group(i)
            if group not in replaced:
                replaced.add(group)
                colored_line = colored_line.replace(group, color + group + SANE)
    return colored_line


def strip_ansi_escape_seq(text: str):
    """
    Strip all ansi escape sequences from the given string.
    From: https://stackoverflow.com/a/14693789
    """

    return ANSI_ESCAPE_RE.sub("", text)
