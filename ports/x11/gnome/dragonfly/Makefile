PORTNAME=	gnome
DISTVERSION=	50
CATEGORIES?=	x11 gnome

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	Metaport for the GNOME 50 desktop (DragonFly Wayland)
WWW=		https://www.gnome.org/

# DragonFly Wayland GNOME 50 batch. Both flavors resolve to the same working
# desktop components; the full flavor adds the GNOME applications ported here.
# GDM is included for the tested seatd-backed login path. PipeWire is pulled
# in by Mutter and Shell, while WirePlumber is explicit because the DragonFly
# session wrapper relies on its PipeWire configuration to start the session
# manager. The GStreamer Pulse sink keeps default audio playback in PipeWire.
# Orca and Sushi are shared dependencies; Evolution, Baobab, Calculator,
# Calendar, Characters, Contacts, Maps, Font Viewer, Clocks, Epiphany, Weather,
# Text Editor, User Docs, Shell Extensions, Connections, Showtime, System
# Monitor, Decibels, Music, Tour, Papers, Loupe, and Yelp are full-only.
FLAVORS=	full lite
FLAVOR?=	full

RUN_DEPENDS=	gdm>=50.1:x11/gdm \
		gnome-session>=50.0:x11/gnome-session \
		gnome-shell>=50.0:x11/gnome-shell \
		gnome-settings-daemon>=50.0:sysutils/gnome-settings-daemon \
		gnome-control-center>=50.0:sysutils/gnome-control-center \
		mutter>=50.0:x11-wm/mutter \
		xdg-desktop-portal-gnome>=50.0:x11/xdg-desktop-portal-gnome \
		wireplumber>=0.5.13:multimedia/wireplumber \
		xdg-user-dirs-gtk>0:x11/xdg-user-dirs-gtk \
		gstreamer1-plugins-pulse>=1.28.1:audio/gstreamer1-plugins-pulse \
		gnome-keyring>0:security/gnome-keyring \
		orca>=50.0:accessibility/orca \
		nautilus>=50.0:x11-fm/nautilus \
		sushi>=50.0:x11-fm/sushi \
		gnome-console>=50.0:x11/gnome-console \
		zenity>0:x11/zenity \
		gvfs>0:filesystems/gvfs \
		localsearch>0:sysutils/localsearch \
		gnome-backgrounds>=50.0:x11-themes/gnome-backgrounds \
		adwaita-icon-theme>=50.0:x11-themes/adwaita-icon-theme \
		cantarell-fonts>0:x11-fonts/cantarell-fonts \
		source-code-pro-ttf>0:x11-fonts/source-code-pro-ttf

USES=		metaport

.if ${FLAVOR} == lite
PKGNAMESUFFIX=	-lite
COMMENT=	Metaport for the GNOME 50 desktop, slimmed to the basics
DESCR=		${.CURDIR}/pkg-descr-lite
.endif

.if ${FLAVOR} == full
RUN_DEPENDS+=	baobab>=50.0:deskutils/baobab \
		decibels>=49.6.1:audio/decibels \
		gnome-calculator>=50.0:math/gnome-calculator \
		gnome-calendar>=50.0:deskutils/gnome-calendar \
		gnome-characters>=50.0:deskutils/gnome-characters \
		evolution>=3.60.0:mail/evolution \
		gnome-contacts>=50.0:deskutils/gnome-contacts \
		gnome-music>=50.0:audio/gnome-music \
		ca_root_nss>=0:security/ca_root_nss \
		gnome-maps>=50.0:deskutils/gnome-maps \
		gnome-font-viewer>=50.0:deskutils/gnome-font-viewer \
		gnome-clocks>=50.0:deskutils/gnome-clocks \
		epiphany>=50.0:www/epiphany \
		loupe>=50.0:graphics/loupe \
		papers>=50.0:graphics/papers \
		gnome-weather>=50.0:deskutils/gnome-weather \
		gnome-text-editor>=50.0:editors/gnome-text-editor \
		gnome-system-monitor>=50.0:sysutils/gnome-system-monitor \
		gnome-user-docs>=50.0:misc/gnome-user-docs \
		gnome-shell-extensions>=50.0:x11/gnome-shell-extensions \
		gnome-connections>=50.0:net/gnome-connections \
		gnome-tour>=50.0:x11/gnome-tour \
		yelp>=49.1:x11/yelp \
		showtime>=50.0:multimedia/showtime
.endif

.include <bsd.port.mk>
