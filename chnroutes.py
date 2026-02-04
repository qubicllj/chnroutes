#!/usr/bin/env python

import re
import urllib3
import sys
import argparse
import math
import textwrap

def generate_ovpn(metric, ipdata):
    rfile=open('routes.txt','w')
    for ip,mask,_ in ipdata:
        route_item="route %s %s net_gateway %d\n"%(ip,mask,metric)
        rfile.write(route_item)
    rfile.close()
    print("Usage: Append the content of the newly created routes.txt to your openvpn config file," \
          " and also add 'max-routes %d', which takes a line, to the head of the file." % (len(ipdata)+20))

def generate_linux_iproute2(metric, ipdata):
    up_template = textwrap.dedent("""\
    #!/bin/bash
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"
    OLDGW=`ip -4 route show | grep '^default' | awk '{{print($3)}}'`

    ip -batch - <<EOF
    {rules}
    EOF
    """)

    down_template = textwrap.dedent("""\
    #!/bin/bash
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"
    CHNROUTE_PATH="/usr/local/sbin"

    ip -batch - <<EOF
    {rules}
    EOF
    """)

    up_rules = ""
    down_rules = ""

    for ip,mask,mask2 in ipdata:
        up_rules += 'route add %s/%s via $OLDGW\n' % (ip,mask2)
        down_rules += 'route del %s/%s \n' % (ip,mask2)

    up_content = up_template.format(rules = up_rules)
    down_content = down_template.format(rules = down_rules)

    upfile=open('add_chnroutes.sh','w')
    downfile=open('remove_chnroutes.sh','w')

    upfile.write(up_content)
    downfile.write(down_content)

    print("Copy the newly created add_chnroutes.sh and remove_chnroutes.sh to the folder /usr/local/sbin/ ")

def generate_linux(metric):
    upscript_header=textwrap.dedent("""\
    #!/bin/bash
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"

    OLDGW=`ip -4 route show | grep '^default' | awk '{{print($3)}}'`

    if [ $OLDGW == '' ]; then
        exit 0
    fi

    if [ ! -e /tmp/vpn_oldgw ]; then
        echo $OLDGW > /tmp/vpn_oldgw
    fi

    """)

    downscript_header=textwrap.dedent("""\
    #!/bin/bash
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"

    OLDGW=`cat /tmp/vpn_oldgw`

    """)

    upfile=open('ip-pre-up','w')
    downfile=open('ip-down','w')

    upfile.write(upscript_header)
    upfile.write('\n')
    downfile.write(downscript_header)
    downfile.write('\n')

    for ip,mask,_ in ipdata:
        upfile.write('route add -net %s netmask %s gw $OLDGW\n'%(ip,mask))
        downfile.write('route del -net %s netmask %s\n'%(ip,mask))

    downfile.write('rm /tmp/vpn_oldgw\n')


    print("For pptp only, please copy the file ip-pre-up to the folder/etc/ppp," \
          "and copy the file ip-down to the folder /etc/ppp/ip-down.d.")

def generate_mac(metric, ipdata):
    upscript_header=textwrap.dedent("""\
    #!/bin/sh
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"

    OLDGW=`netstat -nr -f inet | grep '^default' | awk '{{print($2)}}'`

    if [ ! -e /tmp/pptp_oldgw ]; then
        echo "${OLDGW}" > /tmp/pptp_oldgw
    fi

    dscacheutil -flushcache

    route add 10.0.0.0/8 "${OLDGW}"
    route add 172.16.0.0/12 "${OLDGW}"
    route add 192.168.0.0/16 "${OLDGW}"
    """)

    downscript_header=textwrap.dedent("""\
    #!/bin/sh
    export PATH="/bin:/sbin:/usr/sbin:/usr/bin"

    if [ ! -e /tmp/pptp_oldgw ]; then
            exit 0
    fi

    OLDGW=`cat /tmp/pptp_oldgw`

    route delete 10.0.0.0/8 "${OLDGW}"
    route delete 172.16.0.0/12 "${OLDGW}"
    route delete 192.168.0.0/16 "${OLDGW}"
    """)

    upfile=open('ip-up','w')
    downfile=open('ip-down','w')

    upfile.write(upscript_header)
    upfile.write('\n')
    downfile.write(downscript_header)
    downfile.write('\n')

    for ip,_,mask in ipdata:
        upfile.write('route add %s/%s "${OLDGW}"\n'%(ip,mask))
        downfile.write('route delete %s/%s ${OLDGW}\n'%(ip,mask))

    downfile.write('\n\nrm /tmp/pptp_oldgw\n')
    upfile.close()
    downfile.close()

    print("For pptp on mac only, please copy ip-up and ip-down to the /etc/ppp folder," \
          "don't forget to make them executable with the chmod command.")

def generate_android(metric, ipdata):
    upscript_header=textwrap.dedent("""\
    #!/bin/sh
    alias nestat='/system/xbin/busybox netstat'
    alias grep='/system/xbin/busybox grep'
    alias awk='/system/xbin/busybox awk'
    alias route='/system/xbin/busybox route'

    OLDGW=`netstat -rn | grep '^0.0.0.0' | awk '{{print($2)}}'`

    """)

    downscript_header=textwrap.dedent("""\
    #!/bin/sh
    alias route='/system/xbin/busybox route'

    """)

    upfile=open('vpnup.sh','w')
    downfile=open('vpndown.sh','w')

    upfile.write(upscript_header)
    upfile.write('\n')
    downfile.write(downscript_header)
    downfile.write('\n')

    for ip,mask,_ in ipdata:
        upfile.write('route add -net %s netmask %s gw $OLDGW\n'%(ip,mask))
        downfile.write('route del -net %s netmask %s\n'%(ip,mask))

    upfile.close()
    downfile.close()

    print("Old school way to call up/down script from openvpn client. " \
          "use the regular openvpn 2.1 method to add routes if it's possible")

def download_ip_data():
    #fetch data from apnic
    print("Fetching data from apnic.net, it might take a few minutes, please wait...")
    url=r'https://ftp.apnic.net/apnic/stats/apnic/delegated-apnic-latest'

    http = urllib3.PoolManager()
    response = http.request("GET", url)
    return response.data.decode('utf-8')

def read_ip_data(source):
    try:
        with open(source, 'rb') as f:
            bytes_content = f.read() # Reads as a bytes object
            return bytes_content.decode('utf-8') # Decodes bytes to a string
    except FileNotFoundError:
        print(f"Error: The file '{source}' was not found.")
    except UnicodeDecodeError:
        print(f"Error: Could not decode the file using UTF-8. The file might be encoded differently.")
    except Exception as e:
        print(f"An error occurred: {e}")

def fetch_ip_data(source):
    if (source == "url"):
        data=download_ip_data()
    else:
        data=read_ip_data(source)

    cnregex=re.compile(r'apnic\|cn\|ipv4\|[0-9\.]+\|[0-9]+\|[0-9]+\|a.*',re.IGNORECASE)
    cndata=cnregex.findall(data)

    results=[]

    for item in cndata:
        unit_items=item.split('|')
        starting_ip=unit_items[3]
        num_ip=int(unit_items[4])

        imask=0xffffffff^(num_ip-1)
        #convert to string
        imask=hex(imask)[2:]
        mask=[0]*4
        mask[0]=imask[0:2]
        mask[1]=imask[2:4]
        mask[2]=imask[4:6]
        mask[3]=imask[6:8]

        #convert str to int
        mask=[ int(i,16 ) for i in mask]
        mask="%d.%d.%d.%d"%tuple(mask)

        #mask in *nix format
        mask2=32-int(math.log(num_ip,2))

        results.append((starting_ip,mask,mask2))

    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description="Generate routing rules for vpn.")
    parser.add_argument('-p','--platform',
                        dest='platform',
                        default='openvpn',
                        nargs='?',
                        help="Target platforms, it can be openvpn, mac, linux,"
                        "android. openvpn by default.")
    parser.add_argument('-m','--metric',
                        dest='metric',
                        default=5,
                        nargs='?',
                        type=int,
                        help="Metric setting for the route rules")
    parser.add_argument('-s','--source',
                        dest='source',
                        default='url',
                        nargs='?',
                        help="Ip data source")

    args = parser.parse_args()

    ipdata = fetch_ip_data(args.source)

    if args.platform.lower() == 'openvpn':
        generate_ovpn(args.metric, ipdata)
    elif args.platform.lower() == 'linux-iproute2':
        generate_linux_iproute2(args.metric, ipdata)
    elif args.platform.lower() == 'linux':
        generate_linux(args.metric, ipdata)
    elif args.platform.lower() == 'mac' or args.platform.lower() == 'darwin':
        generate_mac(args.metric, ipdata)
    elif args.platform.lower() == 'android':
        generate_android(args.metric, ipdata)
    else:
        print("Platform %s is not supported."%args.platform, file=sys.stderr)
        exit(1)
