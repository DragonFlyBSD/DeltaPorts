PORTNAME=	dasbus
DISTVERSION=	1.7
CATEGORIES=	devel python
MASTER_SITES=	PYPI
PKGNAMEPREFIX=	${PYTHON_PKGNAMEPREFIX}

MAINTAINER=	desktop@FreeBSD.org
COMMENT=	D-Bus library for Python 3
WWW=		https://github.com/rhinstaller/dasbus

LICENSE=	LGPL21+
LICENSE_FILE=	${WRKSRC}/LICENSE

RUN_DEPENDS=	${PYTHON_PKGNAMEPREFIX}pygobject>0:devel/py-pygobject@${PY_FLAVOR}

USES=		python
USE_PYTHON=	autoplist distutils flavors

NO_ARCH=	yes

.include <bsd.port.mk>
