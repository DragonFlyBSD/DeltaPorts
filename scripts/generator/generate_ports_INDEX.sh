#!/bin/sh

[ -z "${MP}" ] && MP=/usr/ports
[ -z "${DP}" ] && DP=/home/automaton/DPorts

failed=""

rm -f /tmp/INDEX /tmp/failed.ports

/usr/bin/find -s ${MP}/[a-z]* -type d -depth 1 -maxdepth 1 | \
  while read port
  do
      cd ${port}
      out="$(/usr/bin/make PORTSDIR=${DP} PORT_DBDIR=/tmp describe)"
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
