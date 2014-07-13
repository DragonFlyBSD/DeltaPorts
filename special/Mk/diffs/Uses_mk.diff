--- Uses/objc.mk.orig	2014-06-24 17:04:33.593703000 +0000
+++ Uses/objc.mk
@@ -45,14 +45,10 @@ CC=	/usr/bin/clang
 CPP=	/usr/bin/clang-cpp
 CXX=	/usr/bin/clang++
 .else
-BUILD_DEPENDS+=	${LOCALBASE}/bin/clang33:${PORTSDIR}/lang/clang33
-CPP=	${LOCALBASE}/bin/clang-cpp33
-CC=	${LOCALBASE}/bin/clang33
-CXX=	${LOCALBASE}/bin/clang++33
-.if ${OSVERSION} < 900033
-USE_BINUTILS=	yes
-LDFLAGS+=	-B${LOCALBASE}/bin
-.endif
+BUILD_DEPENDS+=	${LOCALBASE}/bin/clang34:${PORTSDIR}/lang/clang34
+CPP=	${LOCALBASE}/bin/clang-cpp34
+CC=	${LOCALBASE}/bin/clang34
+CXX=	${LOCALBASE}/bin/clang++34
 .endif
 .endif
 
