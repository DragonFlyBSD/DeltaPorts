#!/bin/sh
#
# Copy from merge area to DPorts and commit
#
# $1 <category>/<portname>
# $2 portversions,portrevision

CONFFILE=/usr/local/etc/dports.conf

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
   rm -f ${BUSYFILE}
   exit 1
fi

checkdir ()
{
   eval "MYDIR=\$$1"
   if [ ! -d ${MYDIR} ]; then
     echo "The $1 directory (${MYDIR}) does not exist."
     rm -f ${BUSYFILE}
     exit 1
  fi
}

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done

checkdir DPORTS
checkdir MERGED

oldloc=${MERGED}/${1}
newloc=${DPORTS}/${1}
newdir=$(dirname ${newloc})

if [ -d ${newloc} ]; then
  action="Update"
  reflex="to version ${2}"
else
  action="Import"
  reflex="version ${2}"
fi

mkdir -p ${newdir}
cp -r ${oldloc} ${newloc}

NAME=$(echo ${1} | sed -e 's|/|__|g')
mkdir -p -m 777 ${COMQUEUE}
rm -f ${COMQUEUE}/dport.${NAME}
cat > ${COMQUEUE}/dport.${NAME} << EOF
${1}
${action}
${2}
EOF
chmod 777 ${COMQUEUE}/dport.${NAME}
