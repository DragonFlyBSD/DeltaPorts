--- Uses/fortran.mk.orig	2014-09-16 08:16:33.065616000 +0000
+++ Uses/fortran.mk
@@ -18,8 +18,8 @@ fortran_ARGS=	gcc
 .if ${fortran_ARGS} == gcc
 .include "${PORTSDIR}/Mk/bsd.default-versions.mk"
 _GCC_VER=	${GCC_DEFAULT:S/.//}
-BUILD_DEPENDS+=	gfortran${_GCC_VER}:${PORTSDIR}/lang/gcc
-RUN_DEPENDS+=	gfortran${_GCC_VER}:${PORTSDIR}/lang/gcc
+BUILD_DEPENDS+=	gfortran${_GCC_VER}:${PORTSDIR}/lang/gcc${_GCC_VER}
+RUN_DEPENDS+=	gfortran${_GCC_VER}:${PORTSDIR}/lang/gcc${_GCC_VER}
 USE_BINUTILS=	yes
 F77=		gfortran${_GCC_VER}
 FC=		gfortran${_GCC_VER}
