#!/bin/bash

echo "=== Testing Monero API Sources ==="
echo ""

# Test 1: localmonero.co
echo "1. Testing localmonero.co..."
if curl -s --max-time 10 https://localmonero.co/blocks/api/get_stats > /dev/null; then
    echo "   ✓ localmonero.co is responding"
    HEIGHT=$(curl -s https://localmonero.co/blocks/api/get_stats | jq -r '.height')
    echo "   Current height: $HEIGHT"
else
    echo "   ✗ localmonero.co failed"
fi
echo ""

# Test 2: moneroblocks.info
echo "2. Testing moneroblocks.info..."
if curl -s --max-time 10 https://moneroblocks.info/api/get_block_header/3516463 > /dev/null; then
    echo "   ✓ moneroblocks.info is responding"
else
    echo "   ✗ moneroblocks.info failed (500 error)"
fi
echo ""

# Test 3: xmrchain.net
echo "3. Testing xmrchain.net..."
if RESULT=$(curl -s --max-time 10 https://xmrchain.net/api/networkinfo); then
    if echo "$RESULT" | jq . > /dev/null 2>&1; then
        echo "   ✓ xmrchain.net is responding"
        HEIGHT=$(echo "$RESULT" | jq -r '.data.height')
        echo "   Current height: $HEIGHT"
        
        # Try fetching a specific block
        BLOCK=$(curl -s --max-time 10 "https://xmrchain.net/api/block/$HEIGHT")
        if echo "$BLOCK" | jq . > /dev/null 2>&1; then
            HASH=$(echo "$BLOCK" | jq -r '.data.hash')
            echo "   Block hash: ${HASH:0:20}..."
        fi
    else
        echo "   ✗ xmrchain.net returned invalid JSON"
    fi
else
    echo "   ✗ xmrchain.net failed"
fi
echo ""

# Test 4: moneroexplorer.com
echo "4. Testing moneroexplorer.com..."
if RESULT=$(curl -s --max-time 10 https://moneroexplorer.com/api/networkinfo); then
    if echo "$RESULT" | jq . > /dev/null 2>&1; then
        echo "   ✓ moneroexplorer.com is responding"
        HEIGHT=$(echo "$RESULT" | jq -r '.data.height // .height')
        echo "   Current height: $HEIGHT"
    else
        echo "   ✗ moneroexplorer.com returned invalid JSON"
    fi
else
    echo "   ✗ moneroexplorer.com failed"
fi
echo ""

# Test 5: xmr.llcoins.net
echo "5. Testing xmr.llcoins.net..."
if RESULT=$(curl -s --max-time 10 https://xmr.llcoins.net/api/block/last); then
    if echo "$RESULT" | jq . > /dev/null 2>&1; then
        echo "   ✓ xmr.llcoins.net is responding"
        HEIGHT=$(echo "$RESULT" | jq -r '.height')
        HASH=$(echo "$RESULT" | jq -r '.hash')
        echo "   Current height: $HEIGHT"
        echo "   Block hash: ${HASH:0:20}..."
    else
        echo "   ✗ xmr.llcoins.net returned invalid JSON"
    fi
else
    echo "   ✗ xmr.llcoins.net failed"
fi
echo ""

# Test 6: Monero RPC Node
echo "6. Testing node.moneroworld.com RPC..."
if RESULT=$(curl -s --max-time 10 http://node.moneroworld.com:18089/json_rpc \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":"0","method":"get_last_block_header"}'); then
    if echo "$RESULT" | jq . > /dev/null 2>&1; then
        echo "   ✓ Monero RPC node is responding"
        HEIGHT=$(echo "$RESULT" | jq -r '.result.block_header.height')
        HASH=$(echo "$RESULT" | jq -r '.result.block_header.hash')
        echo "   Current height: $HEIGHT"
        echo "   Block hash: ${HASH:0:20}..."
    else
        echo "   ✗ RPC node returned invalid JSON"
    fi
else
    echo "   ✗ Monero RPC node failed"
fi
echo ""

echo "=== Test Complete ==="
