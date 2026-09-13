PORTNAME=	pycairo
DISTVERSION=	1.29.0
CATEGORIES=	graphics python
MASTER_SITES=	PYPI
PKGNAMEPREFIX=	${PYTHON_PKGNAMEPREFIX}

MAINTAINER=	desktop@FreeBSD.org
COMMENT=	Python bindings for Cairo
WWW=		https://pycairo.readthedocs.io \
		https://github.com/pygobject/pycairo

LICENSE=	LGPL21 MPL11
LICENSE_COMB=	multi
LICENSE_FILE_LGPL21=	${WRKSRC}/COPYING-LGPL-2.1
LICENSE_FILE_MPL11=	${WRKSRC}/COPYING-MPL-1.1

BUILD_DEPENDS=	${PYTHON_PKGNAMEPREFIX}meson-python>=0.16.0:devel/meson-python@${PY_FLAVOR}

USES=		compiler:c11 gnome pkgconfig python
USE_PYTHON=	allflavors autoplist pep517
USE_GNOME=	cairo

OPTIONS_DEFINE=	TEST

TEST_TEST_DEPENDS=	${PYTHON_PKGNAMEPREFIX}mypy>=1.18.2:devel/py-mypy@${PY_FLAVOR} \
			${PYTHON_PKGNAMEPREFIX}flake8>=7.0.0:devel/py-flake8@${PY_FLAVOR} \
			${PYTHON_PKGNAMEPREFIX}coverage>=7.2.3:devel/py-coverage@${PY_FLAVOR}
TEST_USE=		python=pytest
TEST_VARS=		PEP517_BUILD_CONFIG_SETTING="-Cbuild-dir=_build" \
			TEST_WRKSRC=${WRKSRC}/_build

post-patch-TEST-on:
	@${REINPLACE_CMD} -e 's|-Dtests=false|-Dtests=true|' ${WRKSRC}/pyproject.toml


.include <bsd.port.pre.mk>

.if ${PYTHON_VER} == ${PYTHON_DEFAULT}
PLIST_FILES=	include/pycairo/py3cairo.h

post-install:
	@${MKDIR} ${STAGEDIR}${PREFIX}/include/pycairo
	${INSTALL_DATA} ${WRKSRC}/cairo/py3cairo.h ${STAGEDIR}${PREFIX}/include/pycairo
.else
post-install:
	@${RM} ${STAGEDIR}${PREFIX}/libdata/pkgconfig/py3cairo.pc
	@${REINPLACE_CMD} -e '\|libdata/pkgconfig/py3cairo.pc|d' \
		${_PYTHONPKGLIST} \
		${STAGEDIR}${PYTHONPREFIX_SITELIBDIR}/pycairo-${DISTVERSION}.dist-info/RECORD
.endif

.include <bsd.port.post.mk>
