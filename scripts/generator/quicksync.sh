#!/bin/sh

while read line; do
./sync1.sh $line
done < /tmp/syncme

