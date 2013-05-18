--- Uses/readline.mk.orig	2013-05-06 15:31:45.000000000 +0000
+++ Uses/readline.mk
@@ -18,6 +18,7 @@ readline_ARGS=	port
 
 .if defined(readline_ARGS) && ${readline_ARGS} == port
 LIB_DEPENDS+=		readline.6:${PORTSDIR}/devel/readline
+CFLAGS+=		-I${LOCALBASE}/include
 CPPFLAGS+=		-I${LOCALBASE}/include
 LDFLAGS+=		-L${LOCALBASE}/lib -lreadline
 .endif
