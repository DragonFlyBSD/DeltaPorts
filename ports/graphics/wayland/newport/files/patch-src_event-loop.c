--- src/event-loop.c.orig	2018-08-21 12:57:12.800360000 +0300
+++ src/event-loop.c	2018-08-21 13:07:44.933192000 +0300
@@ -25,6 +25,7 @@
 
 #include <stddef.h>
 #include <stdio.h>
+#include <err.h>
 #include <errno.h>
 #include <signal.h>
 #include <stdlib.h>
@@ -34,9 +35,9 @@
 #include <fcntl.h>
 #include <sys/socket.h>
 #include <sys/un.h>
-#include <sys/epoll.h>
-#include <sys/signalfd.h>
-#include <sys/timerfd.h>
+#include <sys/types.h>
+#include <sys/event.h>
+#include <sys/time.h>
 #include <unistd.h>
 #include "wayland-util.h"
 #include "wayland-private.h"
@@ -46,7 +47,7 @@
 /** \cond INTERNAL */
 
 struct wl_event_loop {
-	int epoll_fd;
+	int event_fd;
 	struct wl_list check_list;
 	struct wl_list idle_list;
 	struct wl_list destroy_list;
@@ -56,7 +57,7 @@
 
 struct wl_event_source_interface {
 	int (*dispatch)(struct wl_event_source *source,
-			struct epoll_event *ep);
+			struct kevent *ev);
 };
 
 struct wl_event_source {
@@ -77,22 +78,22 @@
 
 static int
 wl_event_source_fd_dispatch(struct wl_event_source *source,
-			    struct epoll_event *ep)
+			    struct kevent *ev)
 {
 	struct wl_event_source_fd *fd_source = (struct wl_event_source_fd *) source;
 	uint32_t mask;
 
 	mask = 0;
-	if (ep->events & EPOLLIN)
+	if (ev->filter == EVFILT_READ)
 		mask |= WL_EVENT_READABLE;
-	if (ep->events & EPOLLOUT)
+	if (ev->filter == EVFILT_WRITE)
 		mask |= WL_EVENT_WRITABLE;
-	if (ep->events & EPOLLHUP)
+	if (ev->flags & EV_EOF)
 		mask |= WL_EVENT_HANGUP;
-	if (ep->events & EPOLLERR)
+	if (ev->flags & EV_ERROR)
 		mask |= WL_EVENT_ERROR;
 
-	return fd_source->func(fd_source->fd, mask, source->data);
+	return fd_source->func(source->fd, mask, source->data);
 }
 
 struct wl_event_source_interface fd_source_interface = {
@@ -103,30 +104,10 @@
 add_source(struct wl_event_loop *loop,
 	   struct wl_event_source *source, uint32_t mask, void *data)
 {
-	struct epoll_event ep;
-
-	if (source->fd < 0) {
-		free(source);
-		return NULL;
-	}
-
 	source->loop = loop;
 	source->data = data;
 	wl_list_init(&source->link);
 
-	memset(&ep, 0, sizeof ep);
-	if (mask & WL_EVENT_READABLE)
-		ep.events |= EPOLLIN;
-	if (mask & WL_EVENT_WRITABLE)
-		ep.events |= EPOLLOUT;
-	ep.data.ptr = source;
-
-	if (epoll_ctl(loop->epoll_fd, EPOLL_CTL_ADD, source->fd, &ep) < 0) {
-		close(source->fd);
-		free(source);
-		return NULL;
-	}
-
 	return source;
 }
 
@@ -162,6 +143,9 @@
 {
 	struct wl_event_source_fd *source;
 
+	struct kevent events[2];
+	unsigned int num_events = 0;
+
 	source = malloc(sizeof *source);
 	if (source == NULL)
 		return NULL;
@@ -170,8 +154,35 @@
 	source->base.fd = wl_os_dupfd_cloexec(fd, 0);
 	source->func = func;
 	source->fd = fd;
+	add_source(loop, &source->base, mask, data);
 
-	return add_source(loop, &source->base, mask, data);
+	if (source->base.fd < 0) {
+		fprintf(stderr, "could not add source\n: %m");
+		free(source);
+		return NULL;
+	}
+
+	if (mask & WL_EVENT_READABLE) {
+		EV_SET(&events[num_events], source->base.fd, EVFILT_READ,
+		      EV_ADD | EV_ENABLE, 0, 0, &source->base);
+		num_events++;
+	}
+
+	if (mask & WL_EVENT_WRITABLE) {
+		EV_SET(&events[num_events], source->base.fd, EVFILT_WRITE,
+		      EV_ADD | EV_ENABLE, 0, 0, &source->base);
+		num_events++;
+	}
+
+	if (kevent(loop->event_fd, events, num_events, NULL, 0, NULL) < 0) {
+		fprintf(stderr, "error adding source %i (%p) to loop %p: %s\n",
+		       source->fd, source, loop, strerror(errno));
+		close(source->base.fd);
+		free(source);
+		return NULL;
+	}
+
+	return &source->base;
 }
 
 /** Update a file descriptor source's event mask
@@ -198,16 +209,22 @@
 wl_event_source_fd_update(struct wl_event_source *source, uint32_t mask)
 {
 	struct wl_event_loop *loop = source->loop;
-	struct epoll_event ep;
+	struct kevent events[2];
+	unsigned int num_events = 0;
+
+	if (mask & WL_EVENT_READABLE) {
+		EV_SET(&events[num_events], source->fd, EVFILT_READ,
+		       EV_ADD | EV_ENABLE, 0, 0, source);
+		num_events++;
+	}
 
-	memset(&ep, 0, sizeof ep);
-	if (mask & WL_EVENT_READABLE)
-		ep.events |= EPOLLIN;
-	if (mask & WL_EVENT_WRITABLE)
-		ep.events |= EPOLLOUT;
-	ep.data.ptr = source;
+	if (mask & WL_EVENT_WRITABLE) {
+		EV_SET(&events[num_events], source->fd, EVFILT_WRITE,
+		       EV_ADD | EV_ENABLE, 0, 0, source);
+		num_events++;
+	}
 
-	return epoll_ctl(loop->epoll_fd, EPOLL_CTL_MOD, source->fd, &ep);
+	return kevent(loop->event_fd, events, num_events, NULL, 0, NULL);
 }
 
 /** \cond INTERNAL */
@@ -221,18 +238,13 @@
 
 static int
 wl_event_source_timer_dispatch(struct wl_event_source *source,
-			       struct epoll_event *ep)
+			       struct kevent *ev)
 {
 	struct wl_event_source_timer *timer_source =
 		(struct wl_event_source_timer *) source;
 	uint64_t expires;
-	int len;
-
-	len = read(source->fd, &expires, sizeof expires);
-	if (!(len == -1 && errno == EAGAIN) && len != sizeof expires)
-		/* Is there anything we can do here?  Will this ever happen? */
-		wl_log("timerfd read error: %m\n");
 
+	expires = ev->data;    /* XXX unused?! */
 	return timer_source->func(timer_source->base.data);
 }
 
@@ -258,18 +270,20 @@
 			wl_event_loop_timer_func_t func,
 			void *data)
 {
+	static int next_timer_id = 0;
 	struct wl_event_source_timer *source;
 
 	source = malloc(sizeof *source);
 	if (source == NULL)
 		return NULL;
-
 	source->base.interface = &timer_source_interface;
-	source->base.fd = timerfd_create(CLOCK_MONOTONIC,
-					 TFD_CLOEXEC | TFD_NONBLOCK);
+	source->base.fd = next_timer_id++;
+	source->base.loop = loop;
+	source->base.data = data;
 	source->func = func;
+	wl_list_init(&source->base.link);
 
-	return add_source(loop, &source->base, WL_EVENT_READABLE, data);
+	return &source->base;
 }
 
 /** Arm or disarm a timer
@@ -291,14 +305,21 @@
 WL_EXPORT int
 wl_event_source_timer_update(struct wl_event_source *source, int ms_delay)
 {
-	struct itimerspec its;
+	struct kevent ev;
+
+	if (ms_delay == 0) {
+		EV_SET(&ev, source->fd, EVFILT_TIMER, EV_DISABLE, 0,
+		       ms_delay, source);
+	} else {
+		EV_SET(&ev, source->fd, EVFILT_TIMER,
+		       EV_ADD | EV_ENABLE | EV_DISPATCH, 0, ms_delay, source);
+	}
 
-	its.it_interval.tv_sec = 0;
-	its.it_interval.tv_nsec = 0;
-	its.it_value.tv_sec = ms_delay / 1000;
-	its.it_value.tv_nsec = (ms_delay % 1000) * 1000 * 1000;
-	if (timerfd_settime(source->fd, 0, &its, NULL) < 0)
+	if (kevent(source->loop->event_fd, &ev, 1, NULL, 0, NULL) < 0) {
+		fprintf(stderr, "could not set kqueue timer: %s",
+		        strerror(errno));
 		return -1;
+	}
 
 	return 0;
 }
@@ -315,20 +336,14 @@
 
 static int
 wl_event_source_signal_dispatch(struct wl_event_source *source,
-				struct epoll_event *ep)
+				struct kevent *ev)
 {
-	struct wl_event_source_signal *signal_source =
-		(struct wl_event_source_signal *) source;
-	struct signalfd_siginfo signal_info;
-	int len;
-
-	len = read(source->fd, &signal_info, sizeof signal_info);
-	if (!(len == -1 && errno == EAGAIN) && len != sizeof signal_info)
-		/* Is there anything we can do here?  Will this ever happen? */
-		wl_log("signalfd read error: %m\n");
+	struct wl_event_source_signal *signal_source;
 
-	return signal_source->func(signal_source->signal_number,
-				   signal_source->base.data);
+	signal_source = (struct wl_event_source_signal *) source;
+
+	return signal_source->func(signal_source->base.fd,
+	                           signal_source->base.data);
 }
 
 struct wl_event_source_interface signal_source_interface = {
@@ -356,12 +371,13 @@
  */
 WL_EXPORT struct wl_event_source *
 wl_event_loop_add_signal(struct wl_event_loop *loop,
-			 int signal_number,
-			 wl_event_loop_signal_func_t func,
-			 void *data)
+			int signal_number,
+			wl_event_loop_signal_func_t func,
+			void *data)
 {
 	struct wl_event_source_signal *source;
 	sigset_t mask;
+	struct kevent ev;
 
 	source = malloc(sizeof *source);
 	if (source == NULL)
@@ -369,15 +385,26 @@
 
 	source->base.interface = &signal_source_interface;
 	source->signal_number = signal_number;
+	source->func = func;
 
 	sigemptyset(&mask);
 	sigaddset(&mask, signal_number);
-	source->base.fd = signalfd(-1, &mask, SFD_CLOEXEC | SFD_NONBLOCK);
 	sigprocmask(SIG_BLOCK, &mask, NULL);
 
-	source->func = func;
+	source->base.fd = 0;
+	add_source(loop, &source->base, WL_EVENT_READABLE, data);
+
+	EV_SET(&ev, signal_number, EVFILT_SIGNAL, EV_ADD | EV_ENABLE, 0, 0,
+	       source);
 
-	return add_source(loop, &source->base, WL_EVENT_READABLE, data);
+	if (kevent(loop->event_fd, &ev, 1, NULL, 0, NULL) < 0) {
+		fprintf(stderr, "error adding signal for %i (%p), %p: %s\n",
+			signal_number, source, loop, strerror(errno));
+		free(source);
+		return NULL;
+	}
+
+	return &source->base;
 }
 
 /** \cond INTERNAL */
@@ -474,15 +501,74 @@
 wl_event_source_remove(struct wl_event_source *source)
 {
 	struct wl_event_loop *loop = source->loop;
+	int ret = 0, saved_errno = 0;
+
+	/*
+	 * Since BSD doesn't treat all event sources as FDs, we need to
+	 * differentiate by source interface.
+	 */
+	if (source->interface == &fd_source_interface && source->fd >= 0) {
+		struct kevent ev[2];
+		int _ret[2], _saved_errno[2];
+
+		/*
+		 * We haven't stored state about the mask used when adding the
+		 * source, so we have to try and remove both READ and WRITE
+		 * filters. One may fail, which is OK. Removal of the source has
+		 * only failed if _both_ kevent() calls fail. We have to do two
+		 * kevent() calls so that we can get independent return values
+		 * for the two kevents.
+		 */
+		EV_SET(&ev[0], source->fd, EVFILT_READ, EV_DELETE, 0, 0,
+		      source);
+		EV_SET(&ev[1], source->fd, EVFILT_WRITE, EV_DELETE, 0, 0,
+		      source);
+
+		_ret[0] = kevent(loop->event_fd, &ev[0], 1, NULL, 0, NULL);
+		_saved_errno[0] = errno;
+		_ret[1] = kevent(loop->event_fd, &ev[1], 1, NULL, 0, NULL);
+		_saved_errno[1] = errno;
+
+		if (_ret[0] >= _ret[1]) {
+			ret = _ret[0];
+			saved_errno = _saved_errno[0];
+		} else {
+			ret = _ret[1];
+			saved_errno = _saved_errno[1];
+		}
 
-	/* We need to explicitly remove the fd, since closing the fd
-	 * isn't enough in case we've dup'ed the fd. */
-	if (source->fd >= 0) {
-		epoll_ctl(loop->epoll_fd, EPOLL_CTL_DEL, source->fd, NULL);
 		close(source->fd);
-		source->fd = -1;
+	} else if (source->interface == &timer_source_interface) {
+		struct kevent ev;
+
+		EV_SET(&ev, source->fd, EVFILT_TIMER, EV_DELETE, 0, 0, source);
+		ret = kevent(loop->event_fd, &ev, 1, NULL, 0, NULL);
+		saved_errno = errno;
+	} else if (source->interface == &signal_source_interface) {
+		struct kevent ev;
+		int signal_number;
+		struct wl_event_source_signal *_source;
+
+		/* Only one kevent() call needed. */
+		_source = (struct wl_event_source_signal *) source;
+		signal_number = _source->signal_number;
+
+		EV_SET(&ev, signal_number, EVFILT_SIGNAL, EV_DELETE, 0, 0,
+		      source);
+		ret = kevent(loop->event_fd, &ev, 1, NULL, 0, NULL);
+		saved_errno = errno;
+	}
+
+	/* Handle any errors from kevent() calls. */
+	if (ret < 0) {
+		fprintf(stderr,
+		        "error removing event (%i) from kqueue: %s\n",
+		        source->fd, strerror(saved_errno));
 	}
 
+	/* Tidy up the source. */
+	source->fd = -1;
+
 	wl_list_remove(&source->link);
 	wl_list_insert(&loop->destroy_list, &source->link);
 
@@ -523,8 +609,8 @@
 	if (loop == NULL)
 		return NULL;
 
-	loop->epoll_fd = wl_os_epoll_create_cloexec();
-	if (loop->epoll_fd < 0) {
+	loop->event_fd = wl_os_kqueue_create_cloexec();
+	if (loop->event_fd < 0) {
 		free(loop);
 		return NULL;
 	}
@@ -556,22 +642,21 @@
 	wl_signal_emit(&loop->destroy_signal, loop);
 
 	wl_event_loop_process_destroy_list(loop);
-	close(loop->epoll_fd);
+	close(loop->event_fd);
 	free(loop);
 }
 
 static bool
 post_dispatch_check(struct wl_event_loop *loop)
 {
-	struct epoll_event ep;
+	struct kevent ev;
 	struct wl_event_source *source, *next;
 	bool needs_recheck = false;
 
-	ep.events = 0;
 	wl_list_for_each_safe(source, next, &loop->check_list, link) {
 		int dispatch_result;
 
-		dispatch_result = source->interface->dispatch(source, &ep);
+		dispatch_result = source->interface->dispatch(source, &ev);
 		if (dispatch_result < 0) {
 			wl_log("Source dispatch function returned negative value!");
 			wl_log("This would previously accidentally suppress a follow-up dispatch");
@@ -625,20 +710,27 @@
 WL_EXPORT int
 wl_event_loop_dispatch(struct wl_event_loop *loop, int timeout)
 {
-	struct epoll_event ep[32];
+	struct kevent ev[64];
 	struct wl_event_source *source;
 	int i, count;
+	struct timespec timeout_spec;
 
 	wl_event_loop_dispatch_idle(loop);
 
-	count = epoll_wait(loop->epoll_fd, ep, ARRAY_LENGTH(ep), timeout);
+	/* timeout is provided in milliseconds */
+	timeout_spec.tv_sec = timeout / 1000;
+	timeout_spec.tv_nsec = (timeout % 1000) * 1000000;
+
+	count = kevent(loop->event_fd, NULL, 0, ev, ARRAY_LENGTH(ev),
+	               (timeout != -1) ? &timeout_spec : NULL);
 	if (count < 0)
 		return -1;
 
 	for (i = 0; i < count; i++) {
-		source = ep[i].data.ptr;
-		if (source->fd != -1)
-			source->interface->dispatch(source, &ep[i]);
+		source = ev[i].udata;
+		if (source->fd != -1) {
+		       source->interface->dispatch(source, &ev[i]);
+		}
 	}
 
 	wl_event_loop_process_destroy_list(loop);
@@ -669,7 +761,7 @@
 WL_EXPORT int
 wl_event_loop_get_fd(struct wl_event_loop *loop)
 {
-	return loop->epoll_fd;
+	return loop->event_fd;
 }
 
 /** Register a destroy listener for an event loop context
