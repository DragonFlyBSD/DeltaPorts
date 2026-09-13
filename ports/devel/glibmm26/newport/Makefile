PORTNAME=	glibmm
DISTVERSION=	2.88.1
CATEGORIES=	devel
MASTER_SITES=	GNOME
PKGNAMESUFFIX=	26
DIST_SUBDIR=	gnome

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	C++ bindings for GLib
WWW=		https://gitlab.gnome.org/GNOME/glibmm

LICENSE=	LGPL21+

USES= compiler:c++17-lang gettext gnome meson perl5 pkgconfig python:build shebangfix tar:xz
USE_GNOME=	glib20 libsigc++30
USE_LDCONFIG=	yes

SHEBANG_GLOB=	*.py

MESON_ARGS= -Dbuild-documentation=false -Dbuild-examples=false

PORTSCOUT=	limitw:1,even
PLIST_SUB=	VERSION=2.68

USE_CXXSTD= c++17
USE_GCC_VERSION= 13
.include <bsd.port.mk>
