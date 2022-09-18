#!/bin/sh

if [ -z "$1" ]; then
    MP=/usr/ports
else
    MP=${1}
fi

failed=""

rm -f /tmp/INDEX /tmp/failed.ports

/usr/bin/find -s ${MP}/[a-z]* -type d -depth 1 -maxdepth 1 | \
  while read port
  do
      cd ${port}
      out="$(/usr/bin/make PORTSDIR=/home/automaton/DPorts PORT_DBDIR=/tmp describe)"
      if [ $? -eq 0 ]; then
	  echo "${out}" >> /tmp/INDEX
      else
	  echo failed: ${port} >> /tmp/failed.ports
      fi
  done

# Overwrite existing INDEX-3
/bin/mv /tmp/INDEX ${MP}/INDEX-3

# Report failures
echo "Failed ports:"
cat /tmp/failed.ports
