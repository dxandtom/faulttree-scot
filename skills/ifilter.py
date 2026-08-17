# -*- coding: utf-8 -*-
from os import remove, path, walk, rename, makedirs, listdir, stat
import sys
from struct import unpack
from zipfile import ZipFile, ZIP_DEFLATED
from re import split, I, search, match, S, M, compile, findall, sub
from time import strftime, strptime, gmtime, localtime
from rarfile import RarFile
from chardet import detect
from datetime import datetime as dd
from collections import Counter, OrderedDict
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False
    print("Warning: openpyxl not installed. Summary sheet will not be generated.")
'''
import docx
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.constants import CONTENT_TYPE as CT
from docx.table import _Cell, Table
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl
from docx.text.paragraph import Paragraph
'''

#import lxml
#from functools import reduce

import olefile
from oletools.oleobj import OleNativeStream
import json
import pandas as pd
import traceback
from functools import reduce
import importlib
importlib.reload(sys)
import threading
import re
#sys.setdefaultencoding('utf-8')
FIRST_KEYS = ['y', 'q', 'isis-af-topology', 'inner-connect', 'transit-vpn', 'hardware-based', 'lsp-loopback', 'md', 'domain-location', 'pce-client-connect', 'msdp', 'abr-summary', 'default-martini', 'alarm-reverse', 'hello', 'lastmember-queryinterval', 'ap-online', 'packet-policy', 'lns', 'dscp', 'preference', 'interface-status-change', 'service6-sec', 'rr-filter', 'dcp', 'subinterface', 'local-ce', 'mbandwidth', 'mpls-rsvp-te-peer', 'parity', 'rdi', 'flow-queue-template', 'vlan-extend', 'authentication-name', 'traffic-policy', 'receive-tolerance', '50GE', 'hard-pipe', 'explicit-path', 'dhcpv6-server-group', 'route-policy', 'pbb', 'ipv6-pool', 'bi-static-ingress', 'forwarding-simulation', 'task-group', 'spmsi-tunnel', 'index', 'crp-policy', 'network-entity', 'fec', 'wtr', 'loop-detection', 'qos-profile', 'inactive-port', 'mpls-ldp-remote', 'jitter-codec', 'encryption-algorithm', 'static-user', 'access-vlan', 'signal-coding', 'execute-default', 'statistics', 'aggregate', 'share-shaping', 'bgp-af-vpn-target', 'link-detect', 'tcp-kind', 'block', 'ascii', 'supernet', 'gvrp', 'default-domain', 'install-module', 'user-interface', 'eap-sim-parameter', 'apply-label', 'client-id', 'patch-diagnose', 'server-port', 'flow-car', 'stub-router', 'vpn-instance-af-ipv6', 'rate-limit-mode', 'source-interface', 'command', 'stream-dual-receiving', 'backup-id', 'vpls-multicast', 'lmp-peer', 'bgp-af-ipv6-flow', 'pd-unshare-only', 'sys-ipsec-policy-template', 'release-agent', 'operation-type', '802.1p-to-vpn', 'pim6', 'pfs', 'process-pst', 'bgp-lsp', 'access-speed', 'local-switch', 'record-file', 'Virtual-Serial', 'nas-serial', 'ddp', 'cspf', 'ike-peer', 'l2-channel-glb', 'mput', 'ds-lite', 'unr', 'access-type', 'authorization-scheme', 'route-filter', 'ike-proposal', 'VC4', 'traffic-statistics', 'mrru', 'port-wred', 'duplex', 'dhcp-user-slot-warning-threshold', 'pure-user-name', 'sequence', 'rule-admin', 'sr-te', 'adv-factor', 'queue-4cos-mapping', 'ssm-map', 'quota-out', 'observe', 'service-sec', 'test', 'domain-list', 'ssh', 'default-route-advertise', 'arp-ping', 'Global-Ima-Group', 'vsi-bgpad', 'ccm', 'rule-name', 'radius-server', 'client-duid', 'binding', 'ip-address', 'cu-mode', 'ti-lfa', 'pki-access-policy', 'enable', 'send-trap', 'pw-tag', 'virtual-access-portextend', 'differential-delay', 'ike-version', 'flash-flood', 'emdi', 'import-source', 'dhcpv6-only', 'quit', 'slow-peer', 'isis-frr', 'cgn-pub-user', 'section', 'multi-instance', 'lsa-arrival-interval', 'loopback-detect', 'reroute', 'dns-url', 'ui-vty', 'bu-suppression', 'backup-rpf-switchover-delay', 'association-type', 'l3-loop-detect', 'option121', 'ipsec-profile', 'frame-format', 'ssm-map6', 'said-node', 'segment-routing', 'mapping-path', 'dhcp-decline', 'help', 'fes', 'tcpsyn-flood', 'as-notation', 'helper-role', 'encapsulation', 'authorization-cmd', 'remote-address', 'service-instance-group', 'qos-profile-timerange', 'link-state', 'login-failed', 'sham-hello', 'Sip', 'startup', 'alarm-mask', 'Virtual-Ethernet', 'T1', 'constraint-rt-filter', 'Nve', 'vlan-host-car', 'data-switch', 'IfmMpIf', 'ecc-public-key', 'map-t-instance', 'vbridge', 'lsr-id', 've-group', 'asbr-summary', 'lpu-group', 'ebgp-interface-sensitive', 'realm-name-delimiter', 'cost-offset', 'ipv4', 'sbfd', 'dsa', 'member-slot', 'min-tx-interval', '50100GE', 'wait-request-time', 'local-mac', 'Cpos-Trunk', 'interface-name', 'idle-code', 'direct-route', 'dustproof', 'netconf-nacm-rule-list-rule', 'l2tp-user', 'tiebreaker', 'link-id', 'remote-backup-profile', 'pce-client', 'backup-dual-receiving', 'mac', 'route-mode', 'calling-number-avp', 'reverse', 'portal-redirect', 'tariff-level-cfg', '200GE', 'script-assistant', 'xpl-rfg', 'xpl-pfx', 'dcn', 'holdtime', 'measure-point', 'bpdu', 'register-source', 'te-class7', 'http-redirect-profile', 'GE', 'save', 'area', 'idle-user-redirect', 'authentication-method-ipv6', 'ma', 'pc-af-map', 'rpf-vector', 'ethernet', 'fast-terminate', 'upBoard', 'qppb-loacl-policy', 'match-tunnel', 'slaac-unshare-only', 'prefix-priority', 'static-bind', 'udp', 'statistics-cycle', 'assistant-cli', 'traffic-policing', 'import', 'ServiceIf', 'ptp-adaptive', 'port-group', 'network', 'avoid-microloop', 'atm-pack-overtime', 'vrrp', 'broadcast-copy', 'user-enable-group', 'erps', 'qos', 'client', 'prefix-assign-mode', 'ima', 'l2tp-authorize', 'hvrp-transport', 'attached-bit', 'tftp-server', 'mst-process-region', 'sm2', 'qppb-policy', 'telnet', 'optimize-algorithm', 'alarm-threshold', 'pwsignal', 'search', 'static-rpf-peer', 'bfd-detect', 'confederation', 'static-route', 'echo-passive', 'aaa-task-group', 'mpls-oam', 'sys-b-evpn', 'mst-process', 'tunnel-id', 'twamp', 'DcnInterface', 'vrrp-id', 'history', 'country', 'rmon', 'private-4-byte-as', 'register-packet-checksum', 'flow-route', 'lsp-exp', 'locator', 'ipoe-server', 'trace', 'vpls-id', 'virtual-access-interface', 'mldp', 'lsp-fragments-extend', 'color-flag', 'forbidden', 'rule-type', 'license', 'port-access-limit', 'rip', 'trunkport', 'cmu-alarm', 'access-limit', 'hotstandby-switch', 'Vlanif', 'responder', 'frr', 'exchange-port', 'qos-mshaping', 'silent-interface', 'abort', 'ike', 'pppoe-server', 'redirect-cpu', 'Virtual-Template', 'wavelength-channel', 'VC12', 'c-rp', 'reboot', 'resv-style', 'traffic-eng', 'revertive', 'lpt', 'as', 'smart-link', 'multicast-suppression', 'dhcpv6-unshare-only', 'port-type', 'user-defined-flow', 'mqc', 'capture-packet', 'bgp-ls', 'ignore-mtu-match', 'flow-mapping', 'FlexE50100G', 'user-vlan', 'va-explicit-path-hop', 'dh', 'vsi-bgp-pw', 'mirror', 'key-string', 'key-id', 'join-prune', 'zero-metric-check', 'bgp-vpn', 'encapsulation-mode', 'user-basic-service-ip-type', 'control-vlan', 'organization', 'isis-link-group', 'vpws-df-election', 'pw-control-word', 'rule', 'vlan-group-trunk', 'timeout', 'dns-search-list', 'suppress-flapping', 'pim', 'vlan-group-fe', 'bas-interface-name', 'arp-safeguard', 'option82-identify', '802.1p', 'preferred-control-word', 'dot1q', 'flow-interface-group', 'external-path', 'uservlan_QinQvlan', 'twoway-loss', 'monitor-group', 'user-priority', 'application-apperceive', 'vpn-instance-af-ipv4-mvpn-ipmsi-tunnel-mpls-te', 'snetconf', 'twamp-light', 'unicast-route', 'service-template', 'tunnel-acct-2867', 'import-channel', 'FlexE', 'target', 'authentication-algorithm', 'loop-detect', 'open', 'node-id', 'transport-address', 'datafill', 'l2-channel-vsi', 'hostdefend', 'filter-lsa-out', 'realm-location', 'authening', 'area-authentication-mode', 'renew-time-percent', 'threshold', 'test-flow', 'domain-id', 'protocol', 'nas', 'pic', 'error-down', 'entropy-label', 'speed', 'optical-tx-power', 'inclusive-provider-tunnel', 'sip-server', 'tunnel-alarm', 'configuration', 'value-added-service', 'bgp-af-vpnv4-multicast', 'fragment-threshold', 'p2p-vsi', 'frame-ipv6', 'basinfo-insert', 'dsdomain', 'switch-mode', 'frame-length', 'send-message', 'is-level', 'admin-vsi', 'email', 'acl-mpls', 'user-group', 'gre', 'suppression', 'bas', 'source-policy', 'ftp-client', 'outbound', 'layer3-subscriber', 'remote-mep', 'lock', 'radius-service', 'monitor6', 'rrpp', 'ne-ip', 'seamless-bfd', 'pnp-package', 'tunnel-selector', 'http-redirect', 'sac', 'sensor-alarm', 'ccm-interval', 'ssid', 'cpos', 'sysname', 'local-service-id', 'datasize', 'rm', 'delay-up', 'acl-ip-pool', 'rpd-family', 'xpl-pfx6', 'aaa-domain', 'opaque-capability', 'summary', 'lcd', 'route', 'test-type', 'ipsec', 'acl-ipv6-pool', 'flow-queue', 'bpdu-tunnel', 'lsa-originate-interval', 'record6', 'register-policy', 'led-flash', 'xpl-community', 'cmu-debug', 'mp', '8021p-outbound', 'serviceid-policy', 'ssl-auth-fail', 'esn', 'lsdb', 'vsi-ipmsi-leaf', 'name', 'client-option18', 'detect', 'permit', 'protocol-group', 'online-exmbist', 'query', 'md5-password', 'te-p2p-template', 'increase-priority', 'path-constraint', 'gre-tunnel-group', 'bgp-af-vpn6-instance', 'hold-up', 'vlink-peer', 'fm', 'config-template', 'erps-ring', 'user-policy', 'in-group', 'echo-interval', 'bgp-af-ls', 'user-max-session', 'rtp-header', 'protect-mode', 'ip-trigger', 'nqa-jitter', 'opcode', 'mpls-exp-outbound', 'FlexE50G', 'management', 'te-class4', 'option-82', 'max-ipuser-reauthtime', 'bgp-af-ipv6', 'destination-address', 'ignore-stp-loopcheck', 'ospf', 'ipv6-enhance', 'flow-bill', 'kill', 'twoway-delay', 'traffic-statistic', 'aftr-name', 'jumboframe', 'test-id', 'state', 'access-ip-limit', 'protected-instance', 'remote-peer', 'screen-length', 'acl-l2', 'clear-records', 'match-type', 'min-echo-rx-interval', 'FlexE100G', 'te-class2', 'cmu-relay', 'vpn-instance-af-ipv4-mvpn-ipmsi-tunnel', 'PW-VE', 'multicastbandwidth', 'user-traffic-statistic', 'vsi-bgp', 'option60', 'split-horizon', 'service-type', 'keychain-keyid', 'test-failtimes', 'jitter-packetnum', 'tcp-algorithm-id', 'service-enhance', 'correlation-analyze', 'lsp-trigger', 'netconf-nacm-rule-list', 'remotehelp', 'vsi-ipmsi-root', 'vp', 'password', 'router', 'lsa-age', 'default-route', 'queue', 'activate', 'priority', 'port-mirroring', 'mac-address-usage', 'trust', 'mkdir', 'flowspec', 'file', 'next', 'Lmpif', 'safe-sync', 'user-security-policy', 'controller', 'option82-relay-mode', 'tcp-mss', 'eap-end', 'cmdalias', 'mld-snooping', 'wlan-switch', 'dns-server', 'access', 'flow-label', 'user-fqdn', 'SerLoc', 'certificate', 'http-hostcar', 'interval', 'remote-ce', 'reauthentication', 'undelete', 'tariff-level', 'task-type', 'nexthop', 'pim-ipv6', 'default-password', 'explicit-null-label', 'code', 'mirror-instance', 'holdoff-timer', 'igmp', 'vrrp-backup', 'alias', 'remote-ip', 'aaa-user-group', 'minimal', 'dcp-inst', 'output-alarm', 'l2-loop-detect', 'ipfpm', 'interface-list', 'frr-policy', 'mtu', 'snmp', 'mvpn', 'dot1x', 'l2', 'tti', 'map', 'bgp-flow-ipv6-vpn-instance', 'ecc', 'rename', 'cmp', 'mpls-tp', 'integrity-algorithm', 'transmission-alarm', 'untagged', 'Gmpls-Uni', 'host-route', 'l2vpn-family', 'fail-percent', 'vpn-instance-af-ipv4', 'trafficpolicy', 'l2-multicast', 'multicast-vlan', 'vrrp6', 'peer-public-key', 'fan', 'srv', 'tc-protection', 'control-session', 'ipv6-warning-threshold', 'nas-port-type', 'client-option60', 'arp-trigger', 'nqa-sla', 'spt-only', 'rpf-prune-delay', 'http', 'crc-error', 'accounting', 'Pos', 'compare-different-as-med', 'mac-limit', 'schedule-trace', 'service', 'cusp-agent', 'prompt', 'flow-route-ipv6', 'attack-detect', 'te-p2mp-template', 'eca-route-type', 'data-link', 'mac-learn-style', 'channel-set', 'car', 'bgp-vrf-session', 'process-sequence', 'flow-queue-4cos-template', 'Ip-Trunk', 'spt-switch-threshold', 'lease', 'isis', 'ssl-policy', 'unspecified-channel', 'check', 'diffserv', 'bgp-af-rpd', 'conflict', 'queue-depth', 'ma-defend', 'crc', 'holdoff', 'lsp-replymode', 'filter-policy', 'permit-domain-list', 'http-url', 'service-policy', 'flow-queue-priomode-template', 'lsp-tetunnel', 'drop-timeout', 'receive-time', 'control-vid', 'flow-route-ipv6-vpn-instance', 'vpn-instance-af-ipv6-mvpn-spmsi-tunnel', 'otn', 'flow-wred-template', 'deny-domain-list', 'cost-style', 'option33', 'dhcp-forcerenew', 'unknown-multicast', 'laser', 'retry', 'acl6-basic', 'tracert-hopfailtimes', 'share-mode', 'mac-authentication', 'unknown-unicast', 'ap-id', 'dhcp-proxy', 'software', 'register-suppression-timeout', 'lsdb-overflow-limit', 'tracert', 'link', 'sftp', 'pppoe-motm', 'mpls-ldp-vpn-instance', 'udp-packet-defend', 'voltage-threshold', 'domain', 'mandatory-lcp', 'revert-members', 'dns-suffix', 'mpls-inner-exp', 'rewrite', 'state-refresh-rate-limit', 'inter-area-segmented', 'Otn', 'goto', 'web-url', 'ipv6-family', 'port-wred-template', 'trouble-shooting', 'hub-mode', 'connect-server', 'vpn-instance', 'tftp', 'access-line-id', 'rpc-message', 'security-key', 'prefix', 'ais', '25GE', 'bgp-af-mvpn', 'bfd', 'vni', 'ingress-lsp', 'nbns', 'sftp-client', 'ftp-server', 'topology', 'netconf', 'vlan-group', 'simulate', 'vlan-miss', 'ipv4-prefix', 'cc', 'interface-mtu', 'version', 'VX-TUNNEL', 'Ima-group', 'auto-rp', 'schedule', 'atm-bundle', 'ipv6-flow', 'bgp-af-evpn', 'proposal', 'virtual-system', 'netconf-nacm-group', 'channel-group', 'combined-sysname', 'add', 'te-set-subtlv', 'link-quality', 'vlan-type', 'time-range', 'icmp-jitter-mode', 'virtual-cluster-access', 'capability-announcement', 'accept', 'user-queue-resource', 'location', 'igp-sync-delay', 'down-when-looped', 'xpl-as', 'domain-map-list', 'flow-statistic', 'verbose', 'icmp-reply', 'sender', 'bill-server', 'gre-group', 'user-bandwidth', 'ldp-sync', 'rule-list-name', 'stelnet', 'acl6-ucl', 'run', 'remote-pw-id', 'ipv6', 'l3vpn', 'bandwidth', 'maxage-lsa', 'sensor-path', 'vendor-class', 'diameter-group', 'color-mode', 'ma-defend-slot', 'autoroute', 'remote-interface', 'tos', 'ssm-mapping', 'packet-reassembly', 'entropy-label-capability', 'diagnose', 'isis-topology', 'realtime-accounting', 'l2vpn', 'algorithm', 'vsi-pwsignal-ldp-pg', 'cut', 'peer-address', 'lsp-nexthop', 'path', 'excluded-ipv6-prefix', 'guard-timer', 'lifetime', 'get', 'lost-measure', 'bgp-flow-vpn-instance', 'process-interface-status', 'power', 'upgrade', 'port-speed', 'routing-table', 'require-router-alert', 'keychain', 'option37-relay-mode', 'local-address', 'records', 'append', 'pki-attribute-group', 'promode', 'ip', 'network-header-length', 'ext-community-change', 'bd', 'rpt-spt', 'switch-delay', 'default', 'pnp', 'IfmTrunkSerialPpp', 'remark', 'link-address', 'oam-bind', 'L2tpv3Inst', 'load-balancing', 'LoopBack', 'bypass', 'fail-ratio', 'trunk-member', 'IfmSerialHdlc', 'broadcast', 'ping', 'vlan', 'billing-server', 'IfmGlobalMpGroupIf', 'sys-bd-evpn', 'te-link', 'p2mp', 'auto-frr', 'access-delay', 'ospfv3-area', 'primary', 'mpls-te-leaflist', 'mpls-te', 'dot1x-template', 'cpos-trunk', 'adm', 'bidirectional', 'loop-free-alternate', 'web-logfile', 'delete', 'protocol-statistics', 'policy-route', 'E3', 'ap-user', 'target-bandwidth', 'vpn-instance-capability', 'target-board', 'user-instance', 'nat', 'global', 'nqa-server', 'Mtunnel', 'bypass-attributes', 'ops', 'probe-interval', 'FlexE200GE', 'dns-redirect', 'web-redirect', 'set', 'vpn-id', 'source-ip', 'c-multicast', 'tariff-queue-mapping', 'access-user', 'digest-length', 'virtual-channel', 'grpc', 'remote-lfa', 'threshold-alarm', 'group-member', 'cusp', 'daa-user', 'bi-static-egress', 'proxy-egress', 'clock', 'bsr-policy', 'mpls-tp-meg', 'IfEthWan', 'ipsec-sa', 'ach', 'portal-server', 'passive-session', 'bgp-vpn-instance-af-ipv4-label-unicast', 'maximum-routes', 'me', 'ma-defend-interface', 'sub-port-queue', 'access-operation', 'loopback', 'destination-port', 'jitter-buffer-cep', 'lmp', 'realmname-parse-direction', 'fast-reroute', 'aggregation', 'port-observing', 'sub-session', 'wavelength', 'management-acl', 'dhcp-server-group', 'local-cross-routing', 'pbr-policy-node', 'va-pw', 'encap-data-enable', 'bgp-af-ipv4-flow', 'e-trunk', 'queue-priority', 'fragment-flood', 'list', 'Fabric-Port', 'robust-count', 'ip-dscp-inbound', 'link-account', 'default-ip-address', 'speed-limit', 'time-delay-detect', 'tos-exp', 'accounting-copy', 'Cpos', 'suppress-reachability', 'nssa', 'sma', 'lsp-type', 'link-protocol', 'dns', 'vpls-pruning-mode', 'upload-config', 'tie-breaking', 'session-group', 'local-bill', 'tnl-policy', 'bgp', 'interface-bandwidth-type', 'user-block', 'propagate', 'flexe', 'traffic-type', 'admin-vrrp6', 'raps-mel', 'observe-filter', 'option', 'es', 'lsp-path-check', 'leaf', 'pm-server', 'ce-ping', 'dhcpv6', 'aaa-author', 'optional-checksum', 'system', 'carrier', 'segment-routing-ipv6', 'timers', 'lsp-length', 'arp-proxy', 'purge-originator-identification', 'te-class-mapping', 'ipsec-transform-ah', 'vbas', 'root-ip', 'diameter', 'lac', 'dsa-public-key', 'esi', 'dsa-key-code', 'pki-domain-pki-cmp-session', 'export', 'ref', 'retransmission-limit', 'tunnel-prefer', 'xpl-filter', 'sendpacket', 'ipv6-trigger', 'tunnel-withdraw-delay', 'virtual-cluster-chassis', 'mep', 'ftn', 'vpn-instance-af-ipv6-mvpn-ipmsi-tunnel', 'session-state-trap', 'local-aaa-server', 'rm-backup-prf', 'if-match', 'pe-vid', 'info-center', 'bandwidth-reference', 'authentication-mode', 'hop-limit', 'route-monitor-group', 'resource', 'shutdown', 'ipv4-labeled-unicast', 'ah', 'detect-multiplier', 'negotiation', 'FC', 'hwtacacs-server', 'dn-bit-check', 'damping', 'domainlist', 'renewal-time', 'user-password', 'tunnel-per-user', 'packet-priority', 'bas-load-balance', 'local-remote', 'dhcpsnp-whitelist', 'hot-standby', 'portswitch', 'copy', 'os', 'graceful-delete', 'web-auth-server', 'dhcp-stb', 'icmp-broadcast-address-echo', 'binary', 'acl-port-pool', 'vsi-ipmsi-root-te', 'bgp-af-flow-vpnv6', 'match', 'roam-character', 'ipv4-family', 'frame-ip', 'pw', 'apply-command-group', 'web', 'snmp-agent', 'domain-name-delimiter', 'sysmac', 'assistant', 'Vp', 'user-disable-group', '100GE', 'port-queue-template', 'destination-group', 'rpki-session', 'protocol-packets', 'cpu-defend-policy', 'ikev2', 'ap', 'response-ac-state', 'aaa-recording', 'slot', 'attr-set', 'tracert-livetime', 'adminuser-priority', 'radius-group', 'load', 'max', 'upload', 'va-tunnel', 'accounting-start-delay', 'diameter-server', 'high-temperature', 'l2vpn-ad-family', 'label', 'attribute', 'mget', 'ip-block', 'classifier', 'bridge-domain', 'bgp-af-mvpnv6', 'ATM', 'flow-wred', 'service-identify-policy', 'ip-type', 'acl6-if', 'pki', 'loadbalance', 'wtr-timer', 'vsi-ipmsi', 'eth-test', 'recycle', 'Ethernet', 'vsi-pwsignal-ldp', 'port', 'authentication', 'pppoe-url', 'tunnel-detect-delay', 'disorder', 'discovery-pmtu-max', 'debug', 'group', 'power-supply', 'state-refresh-ttl', 'pack', 'tunnel-policy', 'sys-vpws-evpn', 'modify', 'aaa-authen', 're-authentication', 'multicast-virtual-scheduling', 'te-class3', 'vlan-group-gve', 'pw-aps', 'acl6-advance', 'wlan', 'label-type', 'app-sec-global', 'service-group', 'testbus-selftest', 'duration', 'close', 'remote-download', 'filter', 'pki-domain', 'reflector', 'client-access-line-id', 'data-packet', 'spf', 'time', 'web-url-profile', 'sys-ipsec-policy', 'module-name', 'ipmsi-tunnel', 'condition', 'MEth', 'NULL', 'E1', 'control-tunnel', 'tunnel-protocol', 'lacp', 'local-source-address', 'payload-compression', 'route-select', 'statistics-timer', 'cache-sa-enable', 'test-aaa', 'aps', 'va-explicit-path', 'verification', 'af-mul', 'radius-test-group', 'mac-learning', 'acl', 'acl4-advance', '400G', 'command-privilege', 'format', 'local-mt', 'l3ve', 'vpn-orf', 'static-rp', 'poison-reverse', 'backup', 'efm', 'Pos-Trunk', 'as-path-limit', 'execute', 'rpf', 'bgp-af-ipv4', 'xpl-interface', 'dn', 'nd-trigger', 'multicast-bas-policy', 'mcp-inst', 'aps-send', 'nacm', 'mapping-mode', 'dhcp-broadcast', 'start', 'attack-trace', 'multicast-domain', 'Eth-Trunk', 'mtu-match', 'pppuser-offline-convert', 'stp', 'ce', 'file-format', 'neighbor-check', 'senderid-tlv-type', 'ppp-force-authtype', 'multicast-instance', 'multicast-list', 'ppp', 'static-cr-lsp', 'statistics-task', 'isolate', 'user-group-queue', 'import-rib', 'controller-ip', 'ipsec-proposal', 'exclude', 'site', 'flexeth', 'search-target', 'security', 'pim6-anycast-rp', 'remote-aps', 'unknown-unicast-suppression', 'sender-address', 'rpd-policy', 'longest-match', 'control-flap', 'tlp', 'authentication-method', 'flow', 'warning-exhaust', 'arp-fake', 'connection', 'allow-address-change', 'radius-client', 'ptplptPort', 'te', 'slave', 'batch-shutdown', 'dhcpv6-follow-ipv6cp', 'affinity', 'dhcp-server', 'trigger-up-delay', 'mode', 'mmi-mode', 'offline-speed', 'transform', 'roam-domain', '10GE', 'delay-suppression', 'outside-vlan', 'broadcast-suppression', 'IfmViewVc', 'vlan-group-40ge', 'default-password-template', 'vlan-swap', 'ntp-service', 'service-identify', 'flexe-group', 'flow-route-vpn-instance', 'upe-upe', 'stop', 'pim-anycast-rp', 'mcast-mib', 'diffserv-mode', 'detect-active-link', 'connect-mode', 'vrf-route-import', 'ip-trunk', 'remote-backup-service', 'mpls-ldp-ipv4', 'flexethmode', 'link-group', 'exit', 'vlanpvc-to-username', 'set-dscp-outer', 'agetime', 'diameter-link', 'IfmSerialPpp', 'subnet', 'timestamp-unit', 'Tunnel', 'status-code-change', 'excluded-ipv6-address', 'garp', 'stub', 'eth-trunk', 'active-route-advertise', 'distribute-weight', 'short-sequence', 'shell', 'min-members', 'bestroute', 'history-command', 'public-key-code', 'vpn-target', 'ipg', 'min-active-links', 'region-name', 'field', 'trunk', 'multicast-topology', 'test-packet', 'max-response-time', 'ignore-ac-state', 'ipv6-address', 'pki-entity', 'acl4-basic', 'advertise', 'fec-eth', 'url-profile', 'record', 'ipoe-service-type', 'Virtual-ODUk', 'ospfv3-frr', 'qoe', 'pwd', 'track', 'ccc', 'port-alarm', 'nqa-testflow', 'bit-error-detection', 'tunnel', 'aaa-accounting', 'mapping-server', 'mandatory-chap', 'mpls-ldp-vpn-instance-ipv4', 'mpls-passive', 'bgp-local-ifnet', 'nd-proxy', 'server', 'manual-set', 'preempt', 'atm-link', 'optical-module', 'aggregate-vlan', 'flow-control', 'test-session', 'recording-mode', 'grouping-identifier', 'session-group-exclude', 'multicast-vpn', 'ospf-area', 'sham-link', 'register-with-probe', 'exchange-mode', 'gmpls-tunnel', 'pppoe', 'reoptimization', 'recover', 'Vbdif', 'gateway', 'rsa-key-code', 'l2-minst', 'vpn-instance-af-ipv4-mvpn-spmsi-tunnel', 'databits', 'vping', 'dhcp', 'return', 'precision', 'sensor-group', 'check-first-as', 'mru', 'ip-pool-group', 'client-option37', 'reset', 'vsi-bgp-site', 'ipsec-transform-ah-esp', 'ng-mvpn', 'signaling', 'remove', 'evpn', 'site-id', 'primary-root', 'oam-mgr', 'rmdir', 'display', 'user-queue', 'behavior', 'upe-npe', 'peer-ipv6', 'local-pw-type', 'grpc-server', 'fec-list', 'capability', 'path-vectors', 'ap-offline', 'sample-interval', 'dir', 'mcp-inst-ach', 'vpn-group', 'forward', 'Serial', 'send-time', 'multicast-mib', 'option-61', 'cd', 'remote-id', 'ssl-verify', 'ip-dscp-outbound', 'pki-domain-ldap', 'vc-type', 'timestamp', 'cfm', 'restart', 'ttl-mode', 'evpl', 'shaping', 'service-security', 'Pw-ve', 'sys-evpn', 'measure', 'packet-size', 'ptp', 'graceful-restart', 'ssm-policy', 'sdh-trace', 'admin-vs', 'mpls-exp-inbound', 'user-group-queue-priomode', 'rebinding-time', 'local-id-type', 'dhcpv6-server', 'radius-attribute', 'cep', 'domain-authentication-mode', 'mpls-l2vpn', 'dn-bit-set', 'is-name', 'community-list', 'set-df', 'nd', 'bras', 'FLEXE', 'user-name', 'port-tx-enabling-delay', 'session', 'admin', 'user-aging', 'rate-limit', 'best-site', 'authentication-scheme', 'lns-group', 'transmit-atm-cells', 'vlan-group-xge', 'fec-node', 'alarm-name', 'areav3', 'border-relay', 'rfc1583', 'cp-acl', 'circuit-cost', 'te-associate-group', 'unzip', 'bgp-af-vpn6', 'user-qos', 'state-refresh-interval', 'FCoE-Port', 'port-queue-alarm', 'evpn-global', 'source-port', 'mtp', 'entity-fault', 'rpf-frr', 'l2tp', '40GE', 'ap-hwtacacss', 'subscription', 'router-id', 'ecc-key-code', 'cmd', 'policy-based-route', 'source-address', 'damp-interface', 'slot-warning-threshold', 'cmu-loopback', 'prepaid-profile', 'phy-number', 'vxlan', 'aaa', 'cmu-analog', 'irb-reoriginated', 'l2-multicast-channel', 'security-password', 'e1', 'rmon-statistics', 'bye', 'xpl-rd', 'single-nexthop', 'vlan-group-ve', 'ipsec-instance', 'esp', 'telemetry', 'source', 'arp-limit', 'rate', 'bmp', 'va-lsp', 'service-class', 'user', 'renegotiation-lcp', 'request', 'apply', 'ip-attribute', 'sub-reason', 'forward-mode', 'assign', 'ripng', 'whitelist', 'least', 'atm', 'local-pw-id', 'traffic-behavior', 'domainname-parse-direction', 'client-option38', 'Global-VE', 'llid', 'multicast-profile', 'stopbits', 'Mp-group', 'cp-config', 'master', 'vpls', 'checkzero', 'header', 'hello-option', 'pki-domain-http', 'disconnect', 'probe-failtimes', 'control-word', 'isis-af-frr', 'rsa', 'igmp-snooping', 'cu-agent', 'tc-notify', 'protocol-tunnel', 'l2protocol-tunnel', 'aaa-said', 'auto-discovery', 'source-lifetime', 'hotkey', 'sec', 'loss-measure', 'deny-domain', 'fragment', 'auto-restore', 'channel-profile', 'evpl-mpls', 'ldap-server', 'debugging', 'out-interface', 'lsp-path', 'configuration-occupied', 'mac-withdraw', 'spatial-multicast', 'switch', 'auto-cost', 'redirect', 'ttl-copymode', 'link-down', 'recording-scheme', 'hwtacacs-user', 'activate-route-tag', 'ftp', 'peer-backup', 'global-gre', 'access-service', 'link-state-family', 'te-class0', 'edsg', 'write-default', 'vsi-id', 'active', 'clear', 'dampening', 'set-overload', 'accounting-service-policy', 'bgp-af-l2vpn-ad', 'netbios-type', 'ipsec-transform-esp', 'T3', 'Atm-Bundle', 'qinq', 'mld', 'destination', 'qinq-vlan', 'environment', 'pos-trunk', 'verify-source', 'community', 'ls', 'mldp-p2mp-tunnel', 'app-sec-slot', 'rpki-limit', 'keepalive', 'permit-domain', 'excluded-ip-address', 'spf-schedule-interval', 'vpn-instance-af-ipv4-mvpn', 'qppb', 'log-peer-change', 'alarm', 'login-user', 'group-name', 'frequency', 'record-interval', 'timer', 'host-packet', 'originating-rp', 'load-balance', 'statistic', 'lspv', 'probe-count', 'local-user', 'vsi', 'embedded-rp', 'avp', 'p2mp-template', 'vc', 'disable', 'bgp-af-flow-vpnv4', 'revision-level', 'accounting-scheme', 'diameter-dwr', 'terminal', 'web-server', 'period', 'te-class6', 'st', 'Global-Mp-Group', 'event', 'reflect', 'service-location', 'scramble', 'vlan-group-ge', 'Stack-Port', 'max-atm-cells', 'vpn', 'lmp-peer-te-link', 'fib-miss-report', 'echo-timeout', 'as-number', 'va-explicit-path-lsptype', 'channel', 'bfd-session', 'bgp-af-vpn-instance', 'any-address-release', 'ipv4-address', 'backward', 'crl', 'record-route', 'radius-attribute-format', 'patch', 'interface-parameter-type', 'user-warning-threshold', 'commit', 'sub-ring', 'edit', '8021p-inbound', 'script', 'weight', 'move', 'PwIf', 'netbios-name-server', 'cmu-switch', 'port-mode', 'include', 'auto-report', 'admin-template', 'pre-shared-key', 'ospfv3', 'peer', 'log', 'ipv6-prefix', 'connect-slot', 'xpl-ext-rt', 'pvc', 'icmp', 'route-tag', 'trusted-ca', 'policy', 'GigabitEthernet', '400GE', 'adjacency-strict-check', 'attack-defend', 'mask', 'syslog', 'tcp-buffer-size', 'verify', 'l2tpv3', 'mip', 'bum-suppression', 'default-user-name', 'bmp-session', 'idle-timeout', 'select-authentication-domain', 'max-sessions', 'replace', 'port-queue', 'zip', 'trap-threshold', 'tail', 'Wdm', 'local-bill-pool', 'direct-routes', 'lsp-remaining-lifetime', 'mac-address', 'y1731', 'bgp-af-ipv4-label-unicast', 'default-user-name-template', 'user-stack', 'soc', 'template', 'smart-clock', 'keep-all-routes', 'inbound', 'min-rx-interval', 'cui', 'ttl', 'reauthorize', 'using', 'read-default', 'te-class1', 'mtu-signalling', 'rm-backup-srv', '50100G', 'rpm-diagnose', 'mst-region', 'leaf-list', 'bgp-af-mdt', 'pvp', 'accounting-mode', 'put', 'protect-group', 'Remote-Ap', 'rollback', 'flag', 'undo', 'traffic', 'multi-delegate', 'pads', 'access-trigger', 'organization-unit', 'bgp-ad', 'board-channel-check', 'service-ha', 'passive', 'rsa-public-key', 'bgp-af-vpn', 'common-name', 'color', 'app-sec-interface', 'vlan-instance', 'authorization-mode', 'redirect-profile', 'multicast', 'dynamic-link-protection', 'vtracert', 'vpn-instance-af-ipv6-mvpn-ipmsi-tunnel-mpls-te', 'range', 'locality', 'lldp', 'lsp-tp', 'Trunk-Serial', 'constant-index', 'holddown-time', 'system-view', 'deterministic-med', 'more', 'pm', 'te-class5', 'site-range', 'sr-te-simulate', 'abnormal-packet-defend', 'admin-vrrp', 'mcp', 'multiplex', 'domainmaplist', 'update', 'collect', 'refresh', 'user-group-case-sensitive', 'hts-instance', 'instance', 'uninstall-module', 'c-bsr', 'ecmp', 'nhrp', 'switch-type', 'radius-no-response', 'sa', 'bi-static-transit', 'hostcar', 'mpls-hqos', 'send-router-alert', 'hash', 'protect-switch', 'reallocate-ip-address', 'pw-template', 'accounting-together', 'nqa', 'mpls-l2vpn-peer', 'sender-enable', 'remote', 'up', 'virtual-access', 'priority-car', 'acl4-ucl', 'eq', 'default-cost', 'bypass-vxlan', 'rpt-prune-delay', 'npe-upe', 'aggregation-ipv6', 'cir', 'acl-if', 'interface-match-check', 'issu', 'ip-pool', 'dumpcore', 'tdm-encapsulation-number', 'allow', 'defaultMd', 'multicast-topology6', 'arp', 'rpf-vector-path', 'blacklist', 'vsi-ldp', 'vpn-instance-af-ipv6-mvpn', 'df-election', 'mprofile', 'bind', 'anycast-rp', 'xpl', 'redirect-domain', 'vlan-mapping', 'revert', 'monitor', 'maximum-link-cost', 'route-distinguisher', 'local-backup', 'qos-local-id', 'mpls-ldp', 'role', 'mpls-l2vpn-ce', 'sys-i-evpn', 'batch-backup', 'hdlc', 'path-type', 'vsi-pwsignal-ldp-pw', 'temperature-threshold', 'compare', 'framed-ip', 'l2tpv3-pw', 'username', 'attack-source-trace', 'span', 'mprofileipv6', 'Phy-Serial', 'ip-warning-threshold', 'lsp-version', 'arp-miss', 'interface', 'delay-measure', 'source-proxy', 'client-option82', 'VBridge', 'ssl', 'gtsm', 'idle-cut', 'ipv4-flow', 'mpls', 'hwtacacs', 'rpki', 'acct-session-id-version', 'reserved', 'port-master', 'ecmp-prefer', 'pw-redundancy', 'l2-topology', 'traffic-separate', 'board', 'grpc-client', 'no-renegotiate', 'jitter-buffer', 'root', 'hierarchical-car', 'cpu-defend', 'vas-policy', '8021p', 'vlan-group-100ge', 'discriminator', 'ospf-frr', 'l2tp-group', 'xpl-ext-soo', 'hts', 'ike-identity', 'lastlistener-queryinterval', 'vsi-ipmsi-root-mldp', 'backoff', 'end-group', 'ne-number', 'cp-rate-limit', 'ldp', 'warning-threshold', 'fqdn', 'maximum', 'parse-priority', 'cdup', 'bit-error-mode', 'bsm', 'resource-template', 'tcp', 'traffic-policy-action', 'task', 'hostname', 'import-route', 'mtrace', 'domain-search-list', 'mtu-negotiate', 'unknown-frame', 'vlan-range', 'twoway-jitter', 'scp']

#strptime等不支持多线程，需要加线程锁
c = threading.RLock()

# ============================================================
# 全局 CLI 边界缓存 - 一次性预计算所有 CLI 命令边界
# Key: 文件路径绝对路径
# Value: [(start_pos, end_pos, command_text), ...] 按 start_pos 升序排列
# ============================================================
_CLI_BOUNDARY_CACHE = {}

def _normalize_cmd(cmd_text):
    """将所有 CLI 命令缩写统一归一化为标准形式
    
    dis/disp/displ/displa/display  → display
    more                           → more (不变)
    dir/sys/telnet 等              → 保持原样
    
    支持命令前有 <device> 提示符的情况，自动跳过前缀后归一化。
    """
    cmd_stripped = cmd_text.strip()
    cmd_lower = cmd_stripped.lower()
    # 匹配 dis*/display 开头（允许前面有 <device> 等提示符）
    # (?:<[^>]+>|\[[^\]]+\])? - 非捕获组：设备名提示符（可选）
    # group(1): 命令名（d/dis/.../display）
    # group(2): 参数
    m = re.match(r'^(?:<[^>]+>|\[[^\]]+\])?\s*(d(?:i(?:s(?:p(?:l(?:a)?)?)?)?)?|display)\s+([\s\S]+)$', cmd_lower)
    if m:
        # 返回归一化后的完整命令：display + 参数
        return 'display ' + m.group(2)
    return cmd_stripped


def build_cli_boundaries(diagData):
    """一次性预计算文件中所有 CLI 命令的边界位置
    
    扫描所有标准 CLI 格式的命令起点，返回排序去重后的边界列表。
    各 extract 函数通过命令关键字查表定位，O(log n) 二分查找替代 O(n) 重复正则扫描。
    
    命令归一化：dis/disp/displ/displa/display → display（便于 find_command_boundaries 前缀匹配）
    """
    if not diagData:
        return []
    
    diagData = diagData.replace('\r\n', '\n').replace('\r', '\n')
    
    # Unconditional debug: show first 5 chars to verify content
    print(f'  [BUILD-BC] CALLED, diagData len={len(diagData)}, first5={repr(diagData[:5])}')
    
    # Debug: show first 50 chars to verify correct content
    if diagData and len(diagData) > 30 and diagData[:1] == "\ufeff":
        print(f'  [FMT-DBG] build_cli_boundaries called with {len(diagData)} chars, starts with: {repr(diagData[:50])}')
    
    all_matches = []
    
    # ============================================================
    # 格式A: === 分隔符包裹 (诊断文件主流格式) - 使用行级别匹配
    #   ═══════════════════
    #                 display xxx
    #   ═══════════════════
    # 修复：逐行扫描，确保 === 分隔符不会跨行匹配
    # ============================================================
    lines = diagData.split('\n')
    line_count = len(lines)
    
    # 预计算每行的长度和累积位置（优化性能）
    line_lens = [len(l) for l in lines]
    line_starts = [0] * (line_count + 1)
    for i in range(line_count):
        line_starts[i + 1] = line_starts[i] + line_lens[i] + 1  # +1 for \n
    
    eq_line_pat = compile(r'^[^\S\n]*=+[^\S\n]*$', M)
    display_line_pat = compile(r'^[^\S\n]*(display\s+\S+(?:\s+\S+)*)[^\S\n]*$', I | M)
    
    # 快速预分类：记录所有 === 行和 display 行的索引
    eq_indices = set()
    display_info = []  # [(line_idx, start_pos, cmd_text), ...]
    
    for i in range(line_count):
        line = lines[i]
        if eq_line_pat.match(line):
            eq_indices.add(i)
        else:
            m = display_line_pat.match(line)
            if m:
                display_info.append((i, line_starts[i], m.group(1).strip()))
    
    # 组合 === display === 模式
    for disp_idx, disp_start, raw_cmd in display_info:
        # 检查前一行和后一行是否是 === 行
        if (disp_idx - 1) in eq_indices and (disp_idx + 1) in eq_indices:
            eq1_idx = disp_idx - 1
            eq2_idx = disp_idx + 1
            start_pos = line_starts[eq1_idx]
            end_line2 = line_starts[eq2_idx + 1] - 1  # -1 because last line doesn't have \n
            cmd_text = _normalize_cmd(raw_cmd)
            all_matches.append((start_pos, end_line2, cmd_text))
    
    count_by_fmt = {'fmt_A(eq+display+eq)': len(all_matches)}
    
    # ============================================================
    # 格式B: === 分隔符包裹，无右分隔符（内容从命令后开始）
    # ============================================================
    fmt_B = r'(?:^|\n)[^\S\n]*=+[^\S\n]*\n[^\S\n]*(display\s+\S+(?:\s+\S+)*)[^\S\n]*\n'
    
    # 格式Eq: ===============display clock===============\n 格式（等号包裹命令在同一行）
    # 匹配等号中间的命令内容，并计算完整的命令行边界
    # 格式：===============display clock===============\n
    # 使用捕获组：命令文本在 group(1)，边界为整个匹配范围
    # 前缀和后缀使用非捕获组
    fmt_eq_cmd = r'(?:=+\s*)(display[^\r\n=]+?)(?:=+\s*\n)'
    
    # 格式C: nicfiledata 多设备合并格式
    # <device> + =HUAWEI= + command
    # 支持两种分隔符格式：
    # 1. ============######HUAWEI#####===========（两边都有=）
    # 2. ================######HUAWEI#######（只有一边有=）
    # 命令行后面可能有换行符，也可能没有（文件末尾）
    # 注意：匹配所有命令行，不限定必须是 display 开头（任何命令都是边界）
    fmt_C1 = r'(?:^|\n)[^\S\n]*<[^>]+>[^\S\n]*\n[^\S\n]*=+######HUAWEI#####=*[^\S\n]*\n([^\n]+)(?:\n|\Z)'
    fmt_C2 = r'(?:^|\n)[^\S\n]*\[~?[^]]+\][^\S\n]*\n[^\S\n]*=+######HUAWEI#####=*[^\S\n]*\n([^\n]+)(?:\n|\Z)'
    
    # 格式D: <device>dis xxx / <device>display xxx / <device>more xxx 等同行格式
    #   只匹配真正的设备名提示符格式，如 <RR-JKBTO-01H>、<HUAWEI>、<NE8000-X16>
    #   设备名格式：字母开头，包含字母、数字、连字符、下划线、点号
    #   不匹配 <ProcId=xxx>、<Slot=x> 等非设备名格式
    # 修复：使用 [^\S\n]* 代替 \s*，避免 \s* 匹配换行符导致跨越两行匹配
    fmt_D = r'(?:^|\n)[^\S\n]*<[A-Za-z][A-Za-z0-9_\-\.]*>[^\S\n]*([^\n]+)'
    
    # 格式D2: [~device]dis xxx / [device]display xxx 等同行格式
    #   只匹配真正的设备名提示符格式，如 [~NE8000]、[JED1-PE-01]、[~HUAWEI]
    #   设备名格式：字母开头，包含字母、数字、连字符、下划线、点号，可能有 ~
    #   不匹配 [LOG-INF]、[DOPRA-xxx] 等非设备名格式
    # 修复：使用 [^\S\n]* 代替 \s*，避免 \s* 匹配换行符导致跨越两行匹配
    fmt_D2 = r'(?:^|\n)[^\S\n]*\[~?[A-Za-z][A-Za-z0-9_\-\.]*\][^\S\n]*([^\n]+)'
    
    global_patterns = [
        (fmt_B, I),
        (fmt_eq_cmd, I),  # ===============display xxx=============== 格式
        (fmt_C1, I),
        (fmt_C2, I),
        (fmt_D, I),
        (fmt_D2, I),
    ]
    
    for pattern_str, flags in global_patterns:
        fm = list(compile(pattern_str, flags).finditer(diagData))
        count_by_fmt[pattern_str[:30]] = len(fm)
        for m in fm:
            start_pos = m.start()
            end_pos = m.end()
            raw_cmd = m.group(1).strip()
            # 归一化：dis*/display/more 变体统一化
            cmd_text = _normalize_cmd(raw_cmd)
            all_matches.append((start_pos, end_pos, cmd_text))
    
    # Debug: print per-format counts (only for files containing 'AG-BDMJK' in first 1000 chars)
    if diagData and 'AG-BDMJK' in diagData[:1000]:
        print(f'  [FMT-DBG] build_cli_boundaries: {count_by_fmt}')
    
    # 去重 + 排序
    seen = set()
    unique_matches = []
    for start_pos, end_pos, cmd_text in all_matches:
        if start_pos not in seen:
            seen.add(start_pos)
            unique_matches.append((start_pos, end_pos, cmd_text))
    
    unique_matches.sort(key=lambda x: x[0])
    
    # 过滤掉非 CLI 命令的边界（如空行、纯数字行等）
    # 所有命令行都应该被识别为边界（不限定必须是 display/dis 等开头）
    # 只过滤掉明显不是命令的内容：空行、太短的单行等
    filtered = [(s, e, c) for s, e, c in unique_matches
                if (e - s) <= 1024 * 1024  # wrapper 过滤
                and len(c.strip()) >= 3]  # 命令至少3个字符
    if len(filtered) != len(unique_matches):
        print(f'Filtered out {len(unique_matches) - len(filtered)} non-CLI boundaries from boundary cache')
    return filtered


def get_cli_boundary_cache(diagFile, diagData):
    """从全局缓存中获取 CLI 边界列表，不存在则预计算并缓存"""
    abs_path = path.abspath(diagFile)
    # Debug: print first 30 chars of diagData for all files
    dbg_prefix = path.basename(diagFile) if '\\' in diagFile or '/' in diagFile else str(diagFile)
    if 'AG-BDMJK' in dbg_prefix or 'AG-BDCKJ' in dbg_prefix:
        print(f'  [BC-CACHE] {dbg_prefix}: diagData len={len(diagData) if diagData else 0}, first30={repr(diagData[:30]) if diagData else "EMPTY"}')
    if abs_path not in _CLI_BOUNDARY_CACHE:
        _CLI_BOUNDARY_CACHE[abs_path] = build_cli_boundaries(diagData)
        if _CLI_BOUNDARY_CACHE[abs_path]:
            print(f'CLI boundary cache built for {path.split(diagFile)[1]}, {len(_CLI_BOUNDARY_CACHE[abs_path])} commands found')
    else:
        if 'AG-BDMJK' in dbg_prefix or 'AG-BDCKJ' in dbg_prefix:
            print(f'  [BC-CACHE] CACHE HIT for {dbg_prefix}, {len(_CLI_BOUNDARY_CACHE[abs_path])} commands (no rebuild)')
    return _CLI_BOUNDARY_CACHE[abs_path]


def strip_cli_separator(text):
    """移除提取内容首尾的纯等号分隔符行（如 '=========' 行）"""
    # 去掉前导等号行
    text = re.sub(r'^(?:\s*=+\s*\n)+', '', text)
    # 去掉尾部等号行
    text = re.sub(r'(?:\n\s*=+\s*)+$', '', text)
    return text.strip()

def clear_cli_boundary_cache(diagFile):
    """清理指定文件的 CLI 边界缓存"""
    abs_path = path.abspath(diagFile)
    _CLI_BOUNDARY_CACHE.pop(abs_path, None)


def find_command_boundaries(cli_boundaries, target_cmd_fragment):
    """在预计算的边界列表中，查找命令文本中包含目标片段的所有边界
    
    支持归一化后的前缀匹配：
    - 搜索 'display logbuffer' 可匹配 'dis logbuffer logbuffer | no'（归一化为 'display logbuffer...'）
    - 搜索 'logfile' 可匹配 'display logfile log.log | no'
    - 搜索 'more' 可匹配 'display more startup'
    """
    results = []
    target_lower = target_cmd_fragment.lower()
    for start_pos, end_pos, cmd_text in cli_boundaries:
        # 归一化后的命令：dis*/display/more 等统一格式，前缀匹配
        cmd_lower = cmd_text.lower()
        if cmd_lower.startswith(target_lower) or target_lower in cmd_lower:
            results.append((start_pos, end_pos, cmd_text))
    return results


def find_next_boundary(cli_boundaries, current_end_pos):
    """查找当前命令之后的下一个命令边界（二分查找）"""
    left, right = 0, len(cli_boundaries) - 1
    result = None
    while left <= right:
        mid = (left + right) // 2
        start_pos = cli_boundaries[mid][0]
        if start_pos > current_end_pos:
            result = start_pos
            right = mid - 1
        else:
            left = mid + 1
    return result


def find_next_boundary_after(cli_boundaries, current_end_pos, current_start_pos):
    """查找当前命令之后的下一个命令边界，确保在 current_start_pos 之后"""
    for start_pos, end_pos, cmd_text in cli_boundaries:
        if start_pos > current_end_pos and start_pos > current_start_pos:
            return start_pos
    return None


class MyThread(threading.Thread):
    def __init__(self, func, args=()):
        super(MyThread,self).__init__()
        self.func = func
        self.args = args

    def run(self):
        self.result = self.func(*self.args)

    def get_result(self):
        try:
            return self.result  # 如果子线程不使用join方法，此处可能会报没有self.result的错误
        except Exception:
            return None
class Word():
    def __init__(self, fileName):
        self.doc = Document(fileName)
        self.saveDir = path.splitext(fileName)[0]

    def getOleClsid(self, oleio):
        id = None
        if hasattr(oleio,'header_clsid') and len(oleio.header_clsid) > 0:
            return oleio.header_clsid
        if hasattr(oleio,'clsid') and len(oleio.clsid) > 0:
            return oleio.clsid
        if hasattr(oleio,'root'):
            return self.getOleClsid(oleio.root)
        return id

    def getParagraphs(self):
        r = []
        for  i in self.doc.paragraphs:
            r.append(i.text)
        return r

    def getTables(self):
        r = []

        for i in self.doc.tables:
            r.append(i)

        return r


    def getImage(self):

        part = self.doc.part
        rels = part.rels
        r = []

        for i, rid in enumerate(rels):
            if rels[rid].reltype == RT.IMAGE:
                img = part.related_parts[rid]

                r.append((img.blob,img.filename,img.partname))
        return r

    def getOleObject(self):

        part = self.doc.part
        rels = part.rels
        r = []

        for i, rid in enumerate(rels):
            if rels[rid].reltype == RT.OLE_OBJECT:
                ole = part.related_parts[rid]
                r.append((ole,rid))
        return r

    def getPackage(self):

        part = self.doc.part
        rels = part.rels
        r = []

        for i, rid in enumerate(rels):
            if rels[rid].reltype == RT.PACKAGE:
                package = part.related_parts[rid]
                r.append((package,rid))
        return r

    def getAttachment(self):
        ole = self.getOleObject()
        r = []
        for i, rid in ole:
            oleio = olefile.OleFileIO(i.blob)
            for oleFileName in oleio.listdir():
                oleFileNameb = oleFileName[0].encode('raw_unicode-escape')


                if oleFileNameb == b'\x01Ole10Native':
                    oleFileStream = oleio.openstream(oleFileName)
                    try:
                        nativeStream = OleNativeStream(oleFileStream.read())
                        #r.append((nativeStream.data,nativeStream.filename.encode('raw_unicode-escape').decode('gb2312'),i.partname,rid))
                        try:
                            #temp_name = path.split(nativeStream.src_path)[1]
                            temp_name = nativeStream.src_path.replace('\\', '/')
                            temp_name = path.split(temp_name)[-1]
                            #print('before: %s ---> after: %s' % (nativeStream.src_path, temp_name))
                            r.append((nativeStream.data,nativeStream.filename.encode('raw_unicode-escape').decode('gb2312'),temp_name,rid))
                        except Exception as e:
                            #temp_name = path.split(i.partname)[1]
                            temp_name = i.partname.replace('\\', '/')
                            temp_name = path.split(temp_name)[-1]
                            r.append((nativeStream.data,nativeStream.filename.encode('raw_unicode-escape').decode('gb2312'),temp_name,rid))
                    finally:
                        oleFileStream.close()

                elif oleFileNameb == b'Package':
                    name,ext =self.GetFileNameByRid(rid)

                    if ext in {'xlsx'}:
                        oleFileStream = oleio.openstream(oleFileName)
                        try:
                            fs = oleFileStream.read()
                            r.append((
                                     fs, name+'.'+ext,i.partname,rid))
                        finally:
                            oleFileStream.close()
                    else:
                        pass
                        #print(ext)
                else:
                    pass
                    #print(oleFileNameb)

        package = self.getPackage()
        for i,rid in package:
            #print(i.content_type)
            r.append((i.blob,'',i.partname,rid))

        return r

    def Table2List(self, table, callCell = None, name='', **kwargs):
        if isinstance(table, docx.table.Table):
            rows = {}
            rowid = 0
            for row in table.rows:
                cells = {}
                cid = 0
                for cell in row.cells:
                    content = []

                    if callCell:
                        r = callCell(cell,**kwargs)
                        if r is not None and len(r) > 0:
                            content.append(r)
                    else:
                        if len(cell.text) > 0:
                            content.append(cell.text)
                        if callCell is None and len(cell.tables) > 0:
                            for cellTable in cell.tables:
                                tmpTalbeList = self.Table2List(cellTable, callCell,**kwargs)
                                if tmpTalbeList is not None and len(tmpTalbeList) > 0:
                                    content.append(tmpTalbeList)

                    if len(content) == 1:
                        content = content[0]
                    if len(content) == 0:
                        cells[cid] = ''
                    else:
                        cells[cid] = content
                    cid += 1

                if len(cells) > 0:
                    if len(cells) == 1 and len(cells[0]) == 0:
                        pass
                    else:
                        rows[rowid] = cells
                        rowid += 1

            if len(rows) == 1:
                if len(rows[0]) == 1:
                    if len(rows[0][0]) == 0:
                        return
            if 'and Requirements' in name:
                print(name)
            table = {'table_%s'%(name):rows}
            return table

    def getAll(self):

        filelist = []

        if not path.exists(self.saveDir):
            makedirs(self.saveDir)
        if type != 'RFC':
            txt = self.getParagraphs()
            if len(txt) > 0:
                with open(path.join(self.saveDir, 'Paragraphs.txt'),mode='wb+') as fw:
                    s = '\n'.join(txt)
                    s = s.encode(encoding='UTF-8')
                    fw.write(s)
                    fw.flush()
                    fw.close()

        tables = self.getTables()
        if len(tables) > 0:
            tableList = list([self.Table2List(x) for x in tables])
            with open(path.join(self.saveDir,'Tables.json'),mode='w+') as fw:
                fw.write(json.dumps(tableList))
                fw.flush()
                fw.close()

        images = self.getImage()
        if len(images) > 0:

            for i in images:
                if i[1] in ['image.emf']:
                    continue

                name = '%s_%s'%(path.splitext(i[2])[0].replace('/','_'),i[1])

                with open(path.join(self.saveDir, name), mode='wb+') as fw:
                    fw.write(i[0])
                    fw.flush()
                    fw.close()

        attachs = self.getAttachment()
        attachrid = {}
        if len(attachs) > 0:
            for i in attachs:
                if len(i[1]) > 0:
                    #name = '%s_%s'%(path.splitext(i[2])[0].replace('/','_'),i[1])
                    name = path.splitext(path.split(i[2])[1])[0]+path.splitext(i[1])[1]
                else:
                    name = i[2].replace('/','_')
                headers = self.getRelHeaders(i[3])
                with open(path.join(self.saveDir, name), mode='wb+') as fw:
                    fw.write(i[0])
                    fw.flush()
                    fw.close()
                    attachrid[i[3]] = name
                    filelist.append(path.join(self.saveDir, name))


        ce = self.getChartExcel()
        if len(ce) > 0:
            for i in ce:
                if len(i[1]) > 0:
                    name = '%s_%s'%(path.splitext(i[2])[0].replace('/', '_'), i[1])
                else:
                    name = i[2].replace('/', '_')

                with open(path.join(self.saveDir, name), mode='wb+') as fw:
                    fw.write(i[0])
                    fw.flush()
                    fw.close()

        dflist = OrderedDict()
        pt = self.getParagraphsAndTables(self.doc, attachrid, dflist=dflist)
        if len(pt) > 0:
            with open(path.join(self.saveDir, 'all.json'), mode='w+') as fw:
                fw.write(json.dumps(pt))
                fw.flush()
                fw.close()
        if len(dflist) > 0:
            excelfn = path.join(self.saveDir, 'tables.xlsx')
            def filterInnerTable(cell):
                if isinstance(cell,list):
                    r = []
                    for i in cell:
                        if isinstance(i,dict):
                            pass
                        else:
                            r.append(i)
                    if len(r) == 0:
                        return ''
                    return r
                return cell
            def toString(x):
                if isinstance(x,list):
                    try:
                        x = list(map(str,x))
                        s = '\n'.join(x)
                    except Exception as e:
                        print('error3',x)
                    return s
                elif isinstance(x,str):
                    return x
                try:
                    return str(x)
                except Exception as e:
                    print('error4',e)
                    return ''
            r = OrderedDict()
            for i in dflist:
                df = pd.DataFrame(dflist[i][0])
                try:
                    df = df.applymap(filterInnerTable)
                    df = df.applymap(toString)
                    r[i] = (df,pd.DataFrame([{0:dflist[i][1]}]))
                except Exception as e:
                    print(e)
                    print('error1',i)
            with pd.ExcelWriter(excelfn) as excel:

                for i in r:
                    try:
                        r[i][1].to_excel(excel,sheet_name=i,index=False,header=False,startrow=0)
                        r[i][0].to_excel(excel,sheet_name=i,index=False,header=False,startrow=1)
                    except Exception as e:
                        print(e)
                        print('error2',i)

        return filelist
    def getLink(self):

        part = self.doc.part
        rels = part.rels
        r = []

        for i, rid in enumerate(rels):

            if rels[rid].reltype == RT.HYPERLINK:
                r.append(rels[rid])
        return r

    def getChart(self):

        part = self.doc.part
        rels = part.rels
        r = []

        for i, rid in enumerate(rels):

            if rels[rid].reltype == RT.CHART:
                r.append(rels[rid])
        return r
    def getChartExcel(self):

        chart = self.getChart()
        r = []
        for i in chart:
            chartP = path.splitext(i.target_part.partname)[0]
            for id,irelated in list(i.target_part.related_parts.items()):
                if irelated.content_type in [CT.SML_SHEET]:
                    r.append((irelated.blob, '', path.join(chartP,irelated.partname)))
        return r

    def doc2docx(f):

        if f:
            if path.splitext(f)[-1] in ['.docx']:
                return f
            newpath = path.splitext(f)[0] + '.docx'
            if path.exists(newpath):
                return newpath

            import win32com
            w = win32com.client.Dispatch('Word.Application')
            w.Visible = 0
            w.DisplayAlerts = 0
            doc = w.Documents.Open(f)
            try:
                doc.SaveAs(newpath, 12, False, "", True, "", False, False, False, False)
                doc.Close()
                w.Quit()
            except Exception as e:
                doc.Close()
                w.Quit()
                print(newpath)
                print(e)
            return newpath

    def iter_block_items(self, parent):

        if isinstance(parent, docx.document.Document):
            parent_elm = parent.element.body
        elif isinstance(parent, _Cell):
            parent_elm = parent._tc
        elif isinstance(parent, docx.text.paragraph.Paragraph):
            parent_elm = parent._p
        else:
            return
            #raise ValueError("something's not right")

        for child in parent_elm.iterchildren():

            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)
            elif isinstance(child, docx.oxml.CT_R):
                if 'w:object' in child.xml:
                    yield child
            else:
                pass
                #print('iter_block_items',type(child))

    def getOleID(self, ctr,attachrid=None):

        r  = set()
        for i in ctr:
            if i.tag == '{%s}OLEObject' % ('urn:schemas-microsoft-com:office:office'):
                idkey = '{%s}id'%(docx.opc.constants.NAMESPACE.OFC_RELATIONSHIPS)
                if idkey in i.attrib:
                    rid = i.attrib[idkey]
                    if attachrid is not None and rid in attachrid:
                        r.add(attachrid[rid])
                    else:
                        r.add(rid)
            r.update(self.getOleID(i, attachrid=attachrid))
        return r
    class HeaderNode:
        def __init__(self,header='',level="Heading"):
            self.header = header
            self.level = level
            self._children = []
            self._p = None

        @property
        def Parent(self):
            return self._p

        def Sub(self,level,header=''):
            element = Word.HeaderNode(header, level)
            self._children.append(element)
            element._p = self
            return element
        def getHeaders(self,t,pre=[]):
            if t == self.header:
                return pre + [self.header]
            for c in self._children:
                h = c.getHeaders(t,pre+[self.header])
                if h:
                    return h
            return None

    def getHeaderTree(self,element,xpath=r'preceding::w:p/w:pPr/w:pStyle[starts-with(@w:val,"Heading")]',contentpath=r'../../w:r/w:t/text()'):
        headers = element.xpath(xpath)

        r = Word.HeaderNode()
        cur = r
        for h in headers:
            level = h.val
            while level <= cur.level and cur.level != r.level:
                    cur = cur.Parent

            t = ''.join(h.xpath(contentpath))
            #print(level,t)
            cur = cur.Sub(level,header=t)

        return r
    def getRelHeaders(self,rid):
        rels = self.doc._body._element.xpath('descendant::*[@r:id="' + rid + '"]/../..')
        if len(rels) > 0:
            rel = rels[0]
            headings = rel.xpath('preceding::w:p/w:pPr/w:pStyle[starts-with(@w:val,"Heading")]/../..')
            if headings:
                headings = [''.join(j.xpath('w:r/w:t/text()')) for j in headings]

                headers = ";".join(self.getHeaderTree(rel).getHeaders(headings[-1])[1:])
                return headers
        return ''

    def getParagraphsAndTables(self, document, attachrid=None,dflist =None,preName='',**kwargs):

        sectionName = 'section_%s_%s'
        r = []
        section = {}
        sectionId = 0
        section[sectionName%(sectionId,'')] = r

        Heading = ''
        id = 0
        name = ''
        headers = ''
        class HeaderNode:
            def __init__(self,header='',level="Heading"):
                self.header = header
                self.level = level
                self._children = []
                self._p = None

            @property
            def Parent(self):
                return self._p

            def Sub(self,level,header=''):
                element = HeaderNode(header, level)
                self._children.append(element)
                element._p = self
                return element
            def getHeaders(self,t,pre=[]):
                if t == self.header:
                    return pre + [self.header]
                for c in self._children:
                    h = c.getHeaders(t,pre+[self.header])
                    if h:
                        return h
                return None

        def getHeaderTree(element,xpath=r'preceding::w:p/w:pPr/w:pStyle[starts-with(@w:val,"Heading")]',contentpath=r'../../w:r/w:t/text()'):
            headers = element.xpath(xpath)

            r = HeaderNode()
            cur = r
            for h in headers:
                level = h.val
                while level <= cur.level and cur.level != r.level:
                        cur = cur.Parent

                t = ''.join(h.xpath(contentpath))
                cur = cur.Sub(level,header=t)

            return r

        for  i in self.iter_block_items(document):
            if isinstance(i,Table):
                HeadingTmp = i._element.xpath('preceding::w:p/w:pPr/w:pStyle[starts-with(@w:val,"Heading1")]/../../w:r/w:t/text()')
                #print(Heading2)
                if len(HeadingTmp) > 0:
                    if 'Operation Steps for Change' not in HeadingTmp[-1]:
                        #print(HeadingTmp)
                        continue
                    #HeadingTmp1 = i._element.xpath('preceding::w:p/w:pPr/w:pStyle[starts-with(@w:val,"Heading")]/../../w:r/w:t/text()')
                    HeadingTmp1 = [''.join(j.xpath('w:r/w:t/text()')) for j in i._element.xpath('preceding::w:p/w:pPr/w:pStyle[starts-with(@w:val,"Heading")]/../..')]

                    subHeading = HeadingTmp1[-1]
                    headers = ";".join(self.getHeaderTree(i._element).getHeaders(subHeading)[1:])
                    #if 'Operation Steps for Change' in HeadingTmp:
                    #print(HeadingTmp)
                    Heading = HeadingTmp[-1]
                    id += 1
                else:
                    continue
                idstr = ''
                if id > 0:
                    idstr = str(id)
                name = subHeading

                sheetName = name
                if sheetName == '':
                    sheetName = 'default_' + Heading
                sheetName = idstr + sheetName[-29:]
                t = self.Table2List(i,callCell=self.getParagraphsAndTables,attachrid=attachrid,name=name,preName=sheetName,dflist=dflist)

                sheetName = preName + sheetName

                if dflist is not None and t is not None and len(t) > 0 and sheetName not in list(dflist.keys()):
                    dflist[sheetName] = (list(list(t.values())[0].values()),headers)

                if t is not None and len(t) > 0:
                    r.append(t)

            elif isinstance(i,Paragraph):

                xmlSec = i._element.xpath('w:pPr/w:sectPr')
                if len(xmlSec) > 0:
                    Heading = ''
                    id = 0
                    sectionId +=1
                    r = []
                    secN = i._element.xpath('following::w:p[1]/w:pPr/w:pStyle[@w:val="Heading1"]/../../w:r/w:t/text()')
                    if len(secN) > 0:
                        secN = secN[0]
                    else:
                        secN = ''
                    section[sectionName%(sectionId,secN)] = r


                c = []
                if len(i.text) > 0:
                    c.append(i.text)

                rc = self.getParagraphsAndTables(i,attachrid=attachrid,preName=preName,dflist=dflist)
                if rc is not None and len(rc) > 0:
                    if isinstance(rc,str):
                        c.append(rc)
                    else:
                        c += rc

                if len(c) > 0:
                    if len(c) == 1 and isinstance(c[0],str):
                        c = c[0]
                    r.append(c)
            elif isinstance(i,docx.oxml.CT_R):
                rc = self.getOleID(i,attachrid)
                if rc is not None and len(rc) > 0:
                    r.append({'file':list(rc)})
            else:

                pass

        if len(section) == 1:
            section = list(section.values())[0]
            if len(section) == 1 and isinstance(section[0],str):
                return section[0]

        return section

    def GetFileNameByRid(self, rid = ''):
        e = self.doc.element
        #e = lxml.etree.fromstring(xmlStr)
        targets = e.xpath('//*[@r:id="%s"]'%(rid))
        wp = '{%s}p'%(e.nsmap['w'])

        def getNearP(e,ptag = wp):
            # tmp = e
            # while tmp is not None and tmp.tag != ptag:
            #     tmp = tmp.getparent()

            tmp = e.xpath('ancestor-or-self::%s[1]'%('w:p'),namespaces = e.nsmap)
            if len(tmp) > 0:
                return tmp[0]
            return


        targets = [getNearP(x) for x in targets]

        targets = list([x for x in targets if x is not None])

        targets = list([x.xpath('w:bookmarkStart[@w:name]') for x in targets])
        targets = list(reduce(lambda x,y:x+y,targets))
        targets = list([x.attrib['{%s}name'%x.nsmap['w']] for x in targets])

        targets = list([search(r'(.+)_(\S+?)\d*$', x).groups() for x in targets])

        return targets[0]
import shutil

# =============================================
# 辅助函数：重复文件检测和设备信息补全
# =============================================

def is_duplicate_file(file_path, base_path, check_lines=5):
    """检测文件是否重复（前N行比较）
    返回 True 如果文件重复，False 如果是新文件
    """
    try:
        if not path.exists(file_path):
            return False
        
        # 读取两个文件的前N行
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f1:
            existing_lines = [f1.readline() for _ in range(check_lines)]
        
        # 清理空白字符后比较
        existing_content = ''.join(line.strip() for line in existing_lines if line.strip())
        
        # 如果已有文件前5行为空，不认为重复
        if not existing_content:
            return False
        
        # 检查当前目录下是否有同名文件，比较内容
        dir_name = path.dirname(file_path)
        base_name = path.basename(file_path)
        stem, ext = path.splitext(base_name)
        
        # 检查是否有同名的已处理文件
        for f in [base_name, f"{stem}_1{ext}", f"{stem}_2{ext}"]:
            check_path = path.join(dir_name, f)
            if path.exists(check_path) and check_path != file_path:
                with open(check_path, 'r', encoding='utf-8', errors='ignore') as f2:
                    new_lines = [f2.readline() for _ in range(check_lines)]
                new_content = ''.join(line.strip() for line in new_lines if line.strip())
                if existing_content and existing_content == new_content:
                    return True
        
        return False
    except Exception as e:
        return False

def get_content_hash(file_path):
    """计算文件内容的 MD5 哈希值（用于去重判断）"""
    try:
        import hashlib
        with open(file_path, 'rb') as f:
            # 大文件只读前8KB+后8KB做哈希，兼顾速度与准确
            hash_obj = hashlib.md5()
            f.seek(0)
            hash_obj.update(f.read(8192))
            f_size = f.seek(0, 2)
            if f_size > 16384:
                f.seek(-8192, 2)
                hash_obj.update(f.read(8192))
            elif 8192 < f_size <= 16384:
                remaining = f_size - 8192
                hash_obj.update(f.read(remaining))
            return hash_obj.hexdigest()
    except:
        return None

def are_files_duplicate(src_path, dst_path):
    """高效判断两个文件是否内容相同：先比大小，再比哈希"""
    try:
        src_size = path.getsize(src_path)
        dst_size = path.getsize(dst_path)
        if src_size != dst_size:
            return False  # 大小不同，肯定不同
    except:
        return False
    # 大小相同，再比哈希
    src_hash = get_content_hash(src_path)
    dst_hash = get_content_hash(dst_path)
    return src_hash is not None and src_hash == dst_hash

def get_content_preview(file_path, lines=5):
    """获取文件前N行的内容预览（用于比较）"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
