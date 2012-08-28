from piped import service, util
import zookeeper


class WatchingZNode(service.PipedService):

    def __init__(self, client, path, callback, matcher=lambda path: True, is_root=True):
        super(WatchingZNode, self).__init__()
        self.callback = callback
        self.path = path
        self.client = client
        self.is_root = is_root
        self.matcher = matcher

        self._children = dict()
        self._exists = False
        self._data = None
        self._node_stat = None
        self._is_watching = False

    def startService(self):
        if self.running:
            return
        super(WatchingZNode, self).startService()
        self._keep_watching()

    def stopService(self):
        self.cleanup()
        super(WatchingZNode, self).stopService()
        if self.parent:
            self.disownServiceParent()

    def cleanup(self):
        self._children = dict()
        self._exists = False
        self._data = None
        self._node_stat = None
        self._is_watching = False

    def _stop_due_to_failure(self, failure):
        self.cleanup()

        if failure.type is zookeeper.NoNodeException:
            if self.is_root:
                return

        self.stopService()

    def _keep_watching(self):
        d, w = self.client.exists_and_watch(self.path)
        self.cancellable(d).addCallback(self._on_exists).addErrback(self._stop_due_to_failure)
        self.cancellable(w).addCallback(self._on_exists_changed).addErrback(self._stop_due_to_failure)

    def _on_exists(self, node_stat):
        self._exists = node_stat is not None
        if self._exists and not self._is_watching:
            self._watch_data_and_children()
        return self._exists

    def _on_exists_changed(self, (event, state, path)):
        # exists triggers even if the node just has a
        # data/child-change.  we're only interested in whether it goes
        # from deleted to existing or vice versa.

        did_exist = self._exists
        self._exists = event != zookeeper.DELETED_EVENT
        if did_exist and self._exists:
            # Wait for the next change.
            self._keep_watching()
            return

        self._is_watching = False

        for child in self._children.values():
            child.stopService()

        self.cleanup()

        if self._exists:
            if not did_exist:
                self._watch_data_and_children()
            self._keep_watching()
        else:
            self.callback(dict(deleted=self.path))
            if self.is_root:
                self._keep_watching()
            else:
                self.stopService()

    def _watch_data_and_children(self):
        if self._is_watching or not self.running:
            return

        self._is_watching = True
        self._watch_data()
        self._watch_children()

    def _watch_data(self):
        d, w = self.client.get_and_watch(self.path)
        self.cancellable(d).addCallback(self._on_data).addErrback(self._stop_due_to_failure)
        self.cancellable(w).addCallback(lambda *_: self._watch_data()).addErrback(self._stop_due_to_failure)

    def _on_data(self, result):
        self._node_data, self._node_stat = result
        self.callback(dict(data=self._node_data, metadata=self._node_stat, path=self.path))

    def _watch_children(self):
        d, w = self.client.get_children_and_watch(self.path)
        self.cancellable(d).addCallback(self._on_children).addErrback(self._stop_due_to_failure)
        self.cancellable(w).addCallback(lambda *_: self._watch_children()).addErrback(self._stop_due_to_failure)

    def _on_children(self, children):
        for child in set(self._children.keys()) - set(children):
            self._children[child].stopService()
            del self._children[child]

        for child in children:
            if child in self._children:
                continue

            # rstrip for the corner case where path is '/'
            full_path = self.path.rstrip('/') + '/' + child
            if not self.matcher(full_path):
                continue

            child_watcher = self.__class__(self.client, full_path, self.callback, matcher=self.matcher, is_root=False)
            child_watcher.setServiceParent(self)
            self._children[child] = child_watcher
