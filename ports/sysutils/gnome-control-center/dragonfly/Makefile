PORTNAME=	gnome-control-center
DISTVERSION=	50.3
CATEGORIES=	sysutils gnome
MASTER_SITES=	GNOME
DIST_SUBDIR=	gnome

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	Control center for the GNOME desktop
WWW=		https://apps.gnome.org/Settings/

LICENSE=	GPLv2+
LICENSE_FILE=	${WRKSRC}/COPYING

BUILD_DEPENDS=	blueprint-compiler>=0.19:devel/blueprint-compiler \
		gnome-settings-daemon>0:sysutils/gnome-settings-daemon \
		gsettings-desktop-schemas>0:devel/gsettings-desktop-schemas \
		tecla>0:x11/tecla
LIB_DEPENDS=	libaccountsservice.so:sysutils/accountsservice \
		libcolord-gtk4.so:graphics/colord-gtk \
		libcolord.so:graphics/colord \
		libcups.so:print/cups \
		libepoxy.so:graphics/libepoxy \
		libfontconfig.so:x11-fonts/fontconfig \
		libgoa-1.0.so:net/gnome-online-accounts \
		libgtop-2.0.so:devel/libgtop \
		libibus-1.0.so:textproc/ibus \
		libpolkit-gobject-1.so:sysutils/polkit \
		libpwquality.so:security/libpwquality \
		libpulse.so:audio/pulseaudio \
		libsecret-1.so:security/libsecret \
		libudisks2.so:sysutils/libudisks \
		libupower-glib.so:sysutils/upower \
		libgudev-1.0.so:devel/libgudev \
		libgcr-4.so:security/gcr \
		libgnutls.so:security/gnutls
RUN_DEPENDS=	cantarell-fonts>=0:x11-fonts/cantarell-fonts \
		gnome-settings-daemon>0:sysutils/gnome-settings-daemon \
		gsettings-desktop-schemas>0:devel/gsettings-desktop-schemas \
		gnome-backgrounds>0:x11-themes/gnome-backgrounds \
		tecla>0:x11/tecla \
		cups-pk-helper>0:print/cups-pk-helper

USES=		compiler:c++11-lib gettext gnome localbase:ldflags meson \
		pkgconfig python:build samba:lib tar:xz xorg gssapi:mit
USE_GNOME=	cairo gdkpixbufextra gnomedesktop4 glib20 gtk40 gsound \
		libadwaita libxml2
USE_XORG=	x11 xi
BINARY_ALIAS=	python3=${PYTHON_VERSION}

MESON_ARGS=	-Dlocation-services=disabled \
		-Dsnap=false \
		-Dtests=false

GLIB_SCHEMAS=	org.gnome.Settings.gschema.xml

PORTSCOUT=	limit:^47\.


post-patch:
	@${REINPLACE_CMD} -e 's|%%PREFIX%%|${LOCALBASE}|' \
		${WRKSRC}/panels/printers/pp-host.c
# sharing requires NetworkManager; wwan requires ModemManager.
	${REINPLACE_CMD} -e "/'sharing'/d" -e "/'wwan'/d" \
		${WRKSRC}/panels/meson.build
# color calibration inhibit: login1 -> ConsoleKit (no logind on DragonFly)
	${REINPLACE_CMD} -e 's|"org.freedesktop.login1"|"org.freedesktop.ConsoleKit"|' \
		-e 's|"/org/freedesktop/login1"|"/org/freedesktop/ConsoleKit/Manager"|' \
		-e 's|"org.freedesktop.login1.Manager"|"org.freedesktop.ConsoleKit.Manager"|' \
		${WRKSRC}/panels/color/cc-color-calibrate.c

.include <bsd.port.mk>
