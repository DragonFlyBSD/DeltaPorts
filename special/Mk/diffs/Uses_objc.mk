--- Uses/objc.mk.orig	2014-10-20 14:53:17 UTC
+++ Uses/objc.mk
@@ -49,9 +49,6 @@ BUILD_DEPENDS+=	${LOCALBASE}/bin/clang34
 CPP=	${LOCALBASE}/bin/clang-cpp34
 CC=	${LOCALBASE}/bin/clang34
 CXX=	${LOCALBASE}/bin/clang++34
-.if ${OSVERSION} < 900033
-USE_BINUTILS=	yes
-LDFLAGS+=	-B${LOCALBASE}/bin
 .endif
 .endif
 .endif
