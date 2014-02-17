--- Uses/libtool.mk.orig	2014-02-11 07:22:03.000000000 -0500
+++ Uses/libtool.mk
@@ -20,6 +20,7 @@ patch-libtool:
 	@${FIND} ${WRKDIR} \( -name configure -or -name ltconfig \)	\
 		-type f | ${XARGS} ${REINPLACE_CMD}			\
 		-e '/link_all_deplibs=/s/=unknown/=no/'			\
+		-e 's,freebsd\*),freebsd\*|dragonfly\*),g'			\
 		-e '/objformat=/s/echo aout/echo elf/'
 
 .if ! ${libtool_ARGS:Moldver}
