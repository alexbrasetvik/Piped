# Copyright (c) 2010-2011, Found IT A/S and Piped Project Contributors.
# See LICENSE for details.
"""
This module contains base implementations and managers for the resource system.
"""
from twisted.plugin import IPlugin
from zope import interface

from piped import plugin, exceptions, providers as piped_providers, plugins


class IResourceProvider(IPlugin):
    """ Providers of resources whose availability depend on the
    runtime environment.

    The resources can go up and down arbitrarily, and can be depended on
    by resource consumers as well as other providers.

    Instantiating a resource provider should not have any
    side-effects. Any failures when checking if the resources can
    indeed be provided must be handled gracefully, but should still be
    reported.
    """

    plugin_manager = interface.Attribute("""Set by the plugin manager that
        loaded the provider.""")

    def configure(self, runtime_environment):
        """ Configure the provider.

        :param runtime_environment: The current :class:`.processing.RuntimeEnvironment`.

        The runtime environment contains instances of a resource manager
        and a dependency manager. The resource provider should then
        register any resources it can provide, as well as request its
        own dependencies.

        .. code-block:: python

            def configure(self, runtime_environment):
                self.runtime_environment = runtime_environment # store this for later use

                # Register the things I can provide.
                for key in runtime_environment.get_configuration_value('some_key', dict()):
                    runtime_environment.resource_manager.register('some_key.%s'%key, provider=self)

        The above implementation registers *self* as a provider of every resource path
        generated by looking for 'some_key' in the service configuration.
        """

    def add_consumer(self, resource_dependency):
        """ Register consumption of a resource, defined by
        *resource_dependency*, allegedly provided by this provider.

        :param resource_dependency: An instance of :class:`.resource.ResourceDependency`

        The resource provider can use resource_dependency.provider to
        access the resource path that were requested. Extra configuration
        options may be piped in resource_dependency.configuration.

        By *allegedly*, we mean that the provider cannot always know
        when the resource will be available or if it ever will, since
        this can depend on the external environment.

        Given the func:`configure` example above, the following may
        be used to provide the requested resource.

        .. code-block:: python

            def add_consumer(self, resource_dependency):
                some_key, key = resource_dependency.provider.split('.')
                some_configuration = self.runtime_environment.get_configuration_value(resource_dependency.provider)

                resource = .... # get or create a resource

                # Optionally use self.runtime_environment to request
                # dependencies needed to satisfy this one.

                resource_dependency.on_resource_ready(resource)

        In non-trivial cases, the resource may become available at a later time, or may
        change availability states. It is up to the `IResourceProvider` to ensure that
        the correct :func:`resource.ResourceDependency.on_resource_ready` or
        :func:`resource.ResourceDependency.on_resource_lost` events are invoked.
        """


class ResourceManager(object):
    """ Manages instances of :class:`piped.resource.IResourceProvider` by
    having a registry of who provides what.

    Providers are identified by *resource paths*. A consumer of a
    resource can request e.g. a resource with the path 'database.foo'
    and specify that it wants a "connection". Later on, a
    database provider can provide that. The "database."-part of the
    path is just for namespacing.

        >>> r = ResourceManager()
        >>> provider = object()
        >>> r.register('foo.bar', provider=provider)

    Registered providers are later retrieved by using :func:`get_provider_or_fail`

        >>> r.get_provider_or_fail('foo.bar') is provider
        True

    """

    def __init__(self):
        self._provider_by_path = dict()

    def configure(self, runtime_environment):
        """ Configures this manager with the runtime environment.

        :type runtime_environment: `.processing.RuntimeEnvironment`
        """
        self.runtime_environment = runtime_environment

    def register(self, resource_path, provider):
        """ Register that the resource identified by *resource_path* is provided by *provider*. """
        self._fail_if_resource_is_already_provided(resource_path, provider)
        self._provider_by_path[resource_path] = provider

    def get_registered_resources(self):
        """ Get a dictionary of currently registered resources in this manager.

        The returned dictionary is a copy, so the caller may safely manipulate it without
        affecting the `ResourceManager`.
        """
        return self._provider_by_path.copy()

    def get_provider_or_fail(self, resource_path):
        """ Get the registered provider for the *resource_path*.

        :raises: `exceptions.UnprovidedResourceError` if the resource_path has no providers.
        """
        self._fail_if_resource_path_is_invalid(resource_path)
        return self._provider_by_path[resource_path]

    def resolve(self, resource_dependency):
        """ Resolve *resource_dependency*.

        The resource dependency is resolved by looking up its provider in the list of
        regitered providers and calling the registered :class:`piped.resource.IResourceProvider`\s
        :func:`piped.resource.IResourceProvider.add_consumer` with *resource_dependency*.

        :param resource_dependency: An unresolved :class:`~.dependencies.ResourceDependency`.
        :raises: :class:`.exceptions.UnprovidedResourceError` if the provider does not exist.
        """
        try:
            provider = self.get_provider_or_fail(resource_dependency.provider)
        except exceptions.UnprovidedResourceError, upe:
            upe.msg = '%s: %s' % (resource_dependency, upe.msg)
            raise # re-raise, and keep the traceback
        provider.add_consumer(resource_dependency)

    def _fail_if_resource_path_is_invalid(self, resource_path):
        if resource_path in self._provider_by_path:
            return

        provided_resources = '\n'.join(repr(path) for path in sorted(self._provider_by_path.keys()))
        e_msg = 'no resource provider for: ' + repr(resource_path)
        detail = ('The requested resource could not be found. '
                  'Following is a list of provided resources: \n' + provided_resources)
        hint = 'Are you loading the right plugins from the right packages?'
        raise exceptions.UnprovidedResourceError(e_msg, detail, hint)

    def _fail_if_resource_is_already_provided(self, resource_path, provider):
        if resource_path not in self._provider_by_path:
            return

        details = dict(
            resource_path=resource_path,
            existing_provider=self._provider_by_path[resource_path].name,
            new_provider=provider.name
        )
        e_msg = 'resource already provided: ' + resource_path
        detail = ('The resource "%(resource_path)s" is already provided by "%(existing_provider)s". '
                  '"%(new_provider)s" was attempted registered as an additional provider.' % details)
        raise exceptions.ProviderConflict(e_msg, detail)


class ProviderPluginManager(plugin.PluginManager):
    plugin_packages = [piped_providers, plugins]
    plugin_interface = IResourceProvider

    def __init__(self):
        super(ProviderPluginManager, self).__init__()
        self.providers = set()

    def configure(self, runtime_environment):
        super(ProviderPluginManager, self).configure(runtime_environment)

        # Instantiate all providers
        for Plugin in self._plugins:
            instance = Plugin()
            instance.plugin_manager = self
            self.providers.add(instance)

        for provider in self.providers:
            provider.configure(runtime_environment)
