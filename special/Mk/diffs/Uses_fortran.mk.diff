--- Uses/fortran.mk.orig	2015-04-29 07:42:37 UTC
+++ Uses/fortran.mk
@@ -16,15 +16,13 @@ fortran_ARGS=	gcc
 .endif
 
 .if ${fortran_ARGS} == gcc
-.include "${PORTSDIR}/Mk/bsd.default-versions.mk"
-_GCC_VER=	${GCC_DEFAULT:S/.//}
-.if ${GCC_DEFAULT} == ${LANG_GCC_IS}
-BUILD_DEPENDS+=	gfortran${_GCC_VER}:${PORTSDIR}/lang/gcc
-RUN_DEPENDS+=	gfortran${_GCC_VER}:${PORTSDIR}/lang/gcc
-.else
+.  if ${DFLYVERSION} < 400105
+_GCC_VER=	47
+.  else
+_GCC_VER=	5
+.  endif
 BUILD_DEPENDS+=	gfortran${_GCC_VER}:${PORTSDIR}/lang/gcc${_GCC_VER}
 RUN_DEPENDS+=	gfortran${_GCC_VER}:${PORTSDIR}/lang/gcc${_GCC_VER}
-.endif
 USE_BINUTILS=	yes
 F77=		gfortran${_GCC_VER}
 FC=		gfortran${_GCC_VER}
