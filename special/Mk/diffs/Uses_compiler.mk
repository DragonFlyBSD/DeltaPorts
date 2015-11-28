--- Uses/compiler.mk.orig	2015-11-28 11:24:04 UTC
+++ Uses/compiler.mk
@@ -66,6 +66,11 @@ _COMPILER_ARGS=	#
 _COMPILER_ARGS+=	features
 .endif
 
+.if ${CC} == cc 
+# This is the DragonFly base compiler, we know it's gcc (for now)
+COMPILER_TYPE=		gcc
+COMPILER_VERSION=	52
+.else
 _CCVERSION!=	${CC} --version
 COMPILER_VERSION=	${_CCVERSION:M[0-9].[0-9]*:C/([0-9]).([0-9]).*/\1\2/g}
 .if ${_CCVERSION:Mclang}
@@ -73,6 +78,7 @@ COMPILER_TYPE=	clang
 .else
 COMPILER_TYPE=	gcc
 .endif
+.endif
 
 ALT_COMPILER_VERSION=	0
 ALT_COMPILER_TYPE=	none
@@ -109,6 +115,12 @@ CHOSEN_COMPILER_TYPE=	gcc
 .endif
 
 .if ${_COMPILER_ARGS:Mfeatures}
+.  if ${CC} == cc && ${CXX} == c++
+   # This is DragonFly's base gcc50 compiler
+   # Use a cache for DF rather than testing each feature.
+COMPILER_FEATURES=	libstdc++ c89 c99 c11 gnu89 gnu99 gnu11 c++98 \
+			c++0x c++11 c++14 gnu++98 gnu++11 dragonfly
+.  else
 _CXXINTERNAL!=	${CXX} -\#\#\# /dev/null 2>&1
 .if ${_CXXINTERNAL:M\"-lc++\"}
 COMPILER_FEATURES=	libc++
@@ -129,6 +141,7 @@ OUTPUT_${std}!=	echo | ${CC} -std=${std}
 COMPILER_FEATURES+=	${std}
 .endif
 .endfor
+.  endif	# DragonFly base compiler
 .endif
 
 .if ${_COMPILER_ARGS:Mc++11-lib}
