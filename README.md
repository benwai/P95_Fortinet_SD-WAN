# P95_Fortinet_SD-WAN
Tool to calculate P95 bandwidth usage based on Fortinet SD-WAN Logs

As a prerequisite you need to have :
- a FortiGate with SD-WAN enabled
- disk logging or Fortianalyzer logging or syslog logging
- [SLA Health Check logging](https://community.fortinet.com/t5/FortiGate/Technical-Tip-How-to-enable-log-for-SLA-in-SD-WAN/ta-p/273571) enabled 

We recommand to set Health Check logging interval to 5 seconds to the SLA associated with you WAN interfaces in order to have accurate data.

```
config sys sdwan
    config health-check
        edit <Performance SLA Name>
            set sla-fail-log-period 5
            set sla-pass-log-period 5
        next
    end
end
```
You can confirm that Health Check logging is working by checking directly the logs on the GUI or with these 3 cli commands :
```
execute log filter category 1
execute log filter field logid 0113022925
execute log display
```
You should obtain logs like this
```
FortiGate # execute log display
21516 logs found.
10 logs returned.
2.0% of logs has been searched.

1: date=2026-03-06 time=17:24:55 eventtime=1772814295629751750 tz="+0100" logid="0113022925" type="event" subtype="sdwan" level="information" vd="root" logdesc="SDWAN SLA information" eventtype="SLA" healthcheck="Google" slatargetid=1 interface="VPN-Cato-1" status="up" latency="8.178" jitter="2.085" packetloss="0.000" moscodec="g711" mosvalue="4.398" inbandwidthavailable="1000.00Mbps" outbandwidthavailable="1000.00Mbps" bibandwidthavailable="2.00Gbps" inbandwidthused="1kbps" outbandwidthused="2kbps" bibandwidthused="3kbps" slamap="0x1" msg="Health Check SLA status."
```

Once you have setup logging on FortiGate, you have to wait **1 week or more** in order to collect enought data to be representative for the customer WAN BW usage.

After the period of logging download the SD-WAN logs directly from FortiGate or FortiAnalyzer or Syslog and use the _P95_bandwidth_fortinet.py_ script
```
usage: P95_bandwidth_fortinet.py [-h] [--output OUTPUT] [--interface INTERFACE [INTERFACE ...]] [--healthcheck HEALTHCHECK] [--verbose] file
```
_interface_ and _healtcheck_ parameters are mandatory, you can specify multiple interfaces if you need to agregates the interfaces bandwidth.  
If _interface_ and _healtcheck_ not specified, the script will show you the available _healthchecks_ and _interfaces_ in the _logfile_ specified.

Here is an example of output
```
% python3 P95_bandwidth_fortinet.py disk-event-sdwan-2026_03_06.log --healthcheck Google --interface port1
[INFO] File           : disk-event-sdwan-2026_03_06.log
[INFO] P95 method     : dynamic exclusion of top 5% samples per day
[INFO] Interface      : port1
[INFO] Healthchk filter: Google

[WARN] Line 64906: missing fields (date/inbandwidthused/outbandwidthused)
[WARN] Line 64907: missing fields (date/inbandwidthused/outbandwidthused)
[WARN] Line 64908: missing fields (date/inbandwidthused/outbandwidthused)
[INFO] Lines parsed   : 103283
[INFO] Lines skipped  : 206567  (interface=206567, healthcheck=0)
[INFO] Errors         : 115

  Interface  : port1
  P95 method : dynamic exclusion of top 5% values per day
               (excluded count = floor(5% x actual sample count for the day))

Date              Samples  Excluded (5%)   Max raw (kbps)   P95 (kbps)   P95 (Mbps)
-----------------------------------------------------------------------------------
2026-02-27           9207            460          1440.00       152.00       0.1520
2026-02-28          14669            733          1500.00       140.00       0.1400
2026-03-01          14665            733           748.00       138.00       0.1380
2026-03-02          14744            737         34160.00       152.00       0.1520
2026-03-03          14815            740          5570.00       159.00       0.1590
2026-03-04          14809            740        467500.00      3860.00       3.8600
2026-03-05          14834            741         61800.00      2760.00       2.7600
2026-03-06           5540            277         41840.00      1310.00       1.3100
-----------------------------------------------------------------------------------
MAX P95                                                        3860.00       3.8600
```
