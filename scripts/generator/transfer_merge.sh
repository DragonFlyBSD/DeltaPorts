#!/bin/sh
#
# Copy from merge area to DPorts and indicate with chit
# Create/overwrite STATUS upon build success
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
checkdir DELTA
checkdir POTENTIAL

oldloc=${POTENTIAL}/${1}
newloc=${DPORTS}/${1}
newdir=$(dirname ${newloc})

mkdir -p ${DELTA}/ports/${1}
chown automaton:automaton ${DELTA}/ports/${1}

STATUSFILE=${DELTA}/ports/${1}/STATUS

if [ -d ${newloc} ]; then
  # This means we built it before and a STATUS file should exist
  # Check to see the version of the previous attempt.
  if [ -f ${STATUSFILE} ]; then
     LASTSUCC=$(grep "Last success:" ${STATUSFILE} | cut -c 15-80)
     if [ "${2}" = "${LASTSUCC}" ]; then
        action="Tweak"
        reflex="version ${2}"
     else
        action="Update"
        reflex="to version ${2}"
     fi
  else
     action="Update"
     reflex="to version ${2}"
  fi
else
  action="Import"
  reflex="version ${2}"
fi

mkdir -p ${newdir}
rm -rf ${newloc}
cp -r ${oldloc} ${newloc}
chown -R automaton:automaton ${newloc}

NAME=$(echo ${1} | sed -e 's|/|__|g')
mkdir -p -m 777 ${COMQUEUE}
rm -f ${COMQUEUE}/dport.${NAME}
cat > ${COMQUEUE}/dport.${NAME} << EOF
${1}
${action}
${2}
EOF
chmod 777 ${COMQUEUE}/dport.${NAME}

# Now update the STATUSFILE
if [ -f ${STATUSFILE} ]; then
   TYPE=$(grep PORT ${STATUSFILE})
   echo ${TYPE} > ${STATUSFILE}
else
   echo "PORT" > ${STATUSFILE}
fi
echo "Last attempt: $2" >> ${STATUSFILE}
echo "Last success: $2" >> ${STATUSFILE}
chown automaton:automaton ${STATUSFILE}

NAME=$(echo ${1} | sed -e 's|/|__|g')
mkdir -p -m 777 ${COMQUEUE}
rm -f ${COMQUEUE}/delta.${NAME}
cat > ${COMQUEUE}/delta.${NAME} << EOF
${1}
Success
${2}
EOF
chmod 777 ${COMQUEUE}/delta.${NAME}
