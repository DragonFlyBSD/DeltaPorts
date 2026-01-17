.if ${OPSYS} == DragonFly
BROKEN= yes
.elif ${OPSYS} == FreeBSD
IGNORE= no
.else
USES+= ssl
.endif
