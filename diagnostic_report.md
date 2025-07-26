# Network Connectivity Diagnostic Report

**Date:** 2025-07-26 19:55 UTC  
**Server:** Hetzner VPS with Tailscale  
**Issue:** Intermittent HTTPS/TLS connection timeouts affecting canary script and tuwunel

## Issue Summary

The server is experiencing selective HTTPS/TLS connection failures. While connections to major sites like Google and GitHub succeed, connections to specific APIs (timeapi.io, worldtimeapi.org) consistently timeout during the TLS handshake phase. This affects both the host system and Docker containers.

## Environment Details

- **Platform:** Debian GNU/Linux on Hetzner VPS
- **Networking:** Tailscale (100.64.64.5) + UFW firewall
- **Containers:** Docker with custom bridge network (172.68.0.0/16)
- **Services Affected:** Canary script, potentially tuwunel Matrix server

## Diagnostic Tests Performed

### Host System Tests

1. **Successful HTTPS connections:**
   - google.com: ✅ TLS 1.3 handshake completes successfully
   - github.com: ✅ TLS 1.3 handshake completes successfully

2. **Failed HTTPS connections:**
   - timeapi.io: ❌ SSL connection timeout (both TLS 1.2 and 1.3)
   - worldtimeapi.org: ❌ Connection reset by peer

3. **TCP connectivity:**
   - timeapi.io:443: ✅ Raw TCP connection succeeds
   - This indicates the issue is specifically with TLS/SSL handshake

### Container Tests (diagnostics container)

1. **Successful HTTPS connections:**
   - google.com: ✅ Works from container
   - github.com: ✅ Works from container

2. **Failed HTTPS connections:**
   - timeapi.io: ❌ Connection timeout (TLS 1.2 and 1.3)
   - Same pattern as host system

### Network Configuration Analysis

1. **Firewall (UFW):**
   - Status: Active
   - Default: deny incoming, **allow outgoing**
   - No specific blocks on port 443
   - Outbound HTTPS traffic is permitted

2. **Network Routes:**
   - Server IP: 65.21.99.202 (Hetzner range)
   - Tailscale: 100.64.64.5 active
   - Docker network: 172.68.0.0/16

3. **DNS Resolution:**
   - Working correctly for all tested domains
   - Using 1.1.1.1 (Cloudflare) as primary resolver

## Root Cause Analysis

The issue appears to be **selective TLS handshake failures** rather than general connectivity problems. Key indicators:

1. **Pattern Specificity:** Only affects certain domains/IPs
2. **Protocol Isolation:** TCP connections work, TLS handshake fails
3. **Consistent Behavior:** Same issue on host and containers
4. **Timeout Nature:** Connections hang during TLS negotiation

### Likely Causes

1. **MTU/Fragmentation Issues:**
   - Large TLS handshake packets may be getting fragmented
   - Some intermediate routers may be dropping fragments

2. **Provider-Level Filtering:**
   - Hetzner or upstream providers may have selective filtering
   - Could be anti-DDoS measures affecting certain IP ranges

3. **Geographic/Network Path Issues:**
   - Specific routing paths to certain destinations may have issues
   - Could be temporary network congestion or misconfiguration

## Tools and Resources Available

- **Network Diagnostics:** curl, dig, nc, iptables, tcpdump
- **Container Tools:** Docker with diagnostics container (nicolaka/netshoot)
- **System Tools:** UFW firewall, Tailscale VPN
- **Package Management:** apt (Debian), with homebrew/conda available per user rules

## Recommended Next Steps

### Immediate Actions

1. **MTU Testing:**
   ```bash
   # Test with reduced MTU
   sudo ip link set dev eth0 mtu 1400
   curl --connect-timeout 10 https://timeapi.io/api/Time/current/zone?timeZone=UTC
   ```

2. **Alternative Time Sources:**
   ```bash
   # Test other time APIs that might work
   curl -s http://worldtimeapi.org/api/timezone/UTC  # HTTP instead of HTTPS
   curl -s https://api.ipgeolocation.io/timezone?tz=UTC
   ```

3. **Canary Script Workaround:**
   - Implement fallback to HTTP endpoints where available
   - Add retry logic with exponential backoff
   - Consider using system time with warning when APIs fail

### Investigation Steps

1. **Network Path Analysis:**
   ```bash
   # Trace route to problematic endpoints
   traceroute timeapi.io
   mtr --report timeapi.io
   ```

2. **Packet Capture:**
   ```bash
   # Capture TLS handshake attempts
   sudo tcpdump -i any -w tls_debug.pcap port 443 and host timeapi.io
   ```

3. **Provider Communication:**
   - Contact Hetzner support about potential IP filtering
   - Check for any network maintenance or known issues
   - Verify if other customers report similar problems

### Long-term Solutions

1. **Multiple Time Sources:**
   - Implement multiple fallback time APIs
   - Use mix of HTTP and HTTPS endpoints
   - Add local NTP as final fallback

2. **Network Configuration:**
   - Consider using different DNS resolvers
   - Test with different MTU settings permanently
   - Monitor for patterns in failures

3. **Monitoring:**
   - Set up automated connectivity tests
   - Log successful/failed connections for pattern analysis
   - Alert on consecutive failures

## Impact Assessment

- **Canary Script:** Currently failing to fetch external time sources
- **Tuwunel:** Potential federation connectivity issues if pattern affects Matrix traffic
- **General Services:** Most HTTPS traffic unaffected, issue appears domain-specific

## Files Modified/Created

- `diagnostic_report.md`: This report
- Previous testing left no permanent changes to system configuration

---

*Report generated by automated diagnostics on 2025-07-26T19:55:24Z*
