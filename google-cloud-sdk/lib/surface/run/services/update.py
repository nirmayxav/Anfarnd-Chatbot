# -*- coding: utf-8 -*- #
# Copyright 2018 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Command for updating env vars and other configuration info."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from googlecloudsdk.api_lib.run import k8s_object
from googlecloudsdk.api_lib.run import traffic
from googlecloudsdk.calliope import base
from googlecloudsdk.command_lib.run import config_changes
from googlecloudsdk.command_lib.run import connection_context
from googlecloudsdk.command_lib.run import exceptions
from googlecloudsdk.command_lib.run import flags
from googlecloudsdk.command_lib.run import messages_util
from googlecloudsdk.command_lib.run import pretty_print
from googlecloudsdk.command_lib.run import resource_args
from googlecloudsdk.command_lib.run import resource_change_validators
from googlecloudsdk.command_lib.run import serverless_operations
from googlecloudsdk.command_lib.run import stages
from googlecloudsdk.command_lib.util.concepts import concept_parsers
from googlecloudsdk.command_lib.util.concepts import presentation_specs
from googlecloudsdk.core.console import progress_tracker


def ContainerArgGroup(release_track=base.ReleaseTrack.GA):
  """Returns an argument group with all per-container update args."""

  help_text = """
    If the --container or --remove-containers flag is specified the following
    arguments may only be specified after a --container flag.
    """
  group = base.ArgumentGroup(help=help_text)
  group.AddArgument(flags.ImageArg(required=False))
  group.AddArgument(flags.PortArg())
  group.AddArgument(flags.Http2Flag())
  group.AddArgument(flags.MutexEnvVarsFlags())
  group.AddArgument(flags.MemoryFlag())
  group.AddArgument(flags.CpuFlag())
  group.AddArgument(flags.CommandFlag())
  group.AddArgument(flags.ArgsFlag())
  group.AddArgument(flags.SecretsFlags())
  group.AddArgument(flags.DependsOnFlag())
  if release_track == base.ReleaseTrack.ALPHA:
    group.AddArgument(flags.AddVolumeMountFlag())
    group.AddArgument(flags.RemoveVolumeMountFlag())
  return group


@base.ReleaseTracks(base.ReleaseTrack.GA)
class Update(base.Command):
  """Update Cloud Run environment variables and other configuration settings."""

  detailed_help = {
      'DESCRIPTION': """\
          {description}
          """,
      'EXAMPLES': """\
          To update one or more env vars:

              $ {command} myservice --update-env-vars=KEY1=VALUE1,KEY2=VALUE2
         """,
  }

  @staticmethod
  def CommonArgs(parser, add_container_args=True):
    # Flags specific to managed CR
    managed_group = flags.GetManagedArgGroup(parser)
    flags.AddBinAuthzPolicyFlags(managed_group)
    flags.AddBinAuthzBreakglassFlag(managed_group)
    flags.AddCloudSQLFlags(managed_group)
    flags.AddCmekKeyFlag(managed_group)
    flags.AddCmekKeyRevocationActionTypeFlag(managed_group)
    flags.AddCpuThrottlingFlag(managed_group)
    flags.AddEgressSettingsFlag(managed_group)
    flags.AddEncryptionKeyShutdownHoursFlag(managed_group)
    flags.AddRevisionSuffixArg(managed_group)
    flags.AddSandboxArg(managed_group)
    flags.AddSessionAffinityFlag(managed_group)
    flags.AddStartupCpuBoostFlag(managed_group)
    flags.AddVpcConnectorArgs(managed_group)

    # Flags specific to connecting to a cluster
    cluster_group = flags.GetClusterArgGroup(parser)
    flags.AddEndpointVisibilityEnum(cluster_group)
    flags.AddConfigMapsFlags(cluster_group)

    # Flags not specific to any platform
    service_presentation = presentation_specs.ResourcePresentationSpec(
        'SERVICE',
        resource_args.GetServiceResourceSpec(prompt=True),
        'Service to update the configuration of.',
        required=True,
        prefixes=False,
    )
    if add_container_args:
      flags.AddMutexEnvVarsFlags(parser)
      flags.AddMemoryFlag(parser)
    flags.AddConcurrencyFlag(parser)
    flags.AddTimeoutFlag(parser)
    flags.AddAsyncFlag(parser)
    flags.AddLabelsFlags(parser)
    flags.AddGeneralAnnotationFlags(parser)
    flags.AddMinInstancesFlag(parser)
    flags.AddMaxInstancesFlag(parser)
    if add_container_args:
      flags.AddCommandFlag(parser)
      flags.AddArgsFlag(parser)
      flags.AddPortFlag(parser)
      flags.AddCpuFlag(parser)
    flags.AddNoTrafficFlag(parser)
    flags.AddDeployTagFlag(parser)
    flags.AddServiceAccountFlag(parser)
    if add_container_args:
      flags.AddImageArg(parser, required=False)
    flags.AddClientNameAndVersionFlags(parser)
    flags.AddIngressFlag(parser)
    if add_container_args:
      flags.AddHttp2Flag(parser)
      flags.AddSecretsFlags(parser)
    concept_parsers.ConceptParser([service_presentation]).AddToParser(parser)
    # No output by default, can be overridden by --format
    parser.display_info.AddFormat('none')

  @staticmethod
  def Args(parser):
    Update.CommonArgs(parser)

  def Run(self, args):
    """Update the service resource.

       Different from `deploy` in that it can only update the service spec but
       no IAM or Cloud build changes.

    Args:
      args: Args!

    Returns:
      googlecloudsdk.api_lib.run.Service, the updated service
    """
    changes = flags.GetServiceConfigurationChanges(args, self.ReleaseTrack())
    if not changes or (
        len(changes) == 1
        and isinstance(
            changes[0], config_changes.SetClientNameAndVersionAnnotationChange
        )
    ):
      raise exceptions.NoConfigurationChangeError(
          'No configuration change requested. '
          'Did you mean to include the flags `--update-env-vars`, '
          '`--memory`, `--concurrency`, `--timeout`, `--connectivity`, '
          '`--image`?'
      )
    changes.insert(
        0,
        config_changes.DeleteAnnotationChange(
            k8s_object.BINAUTHZ_BREAKGLASS_ANNOTATION
        ),
    )
    changes.append(
        config_changes.SetLaunchStageAnnotationChange(self.ReleaseTrack())
    )

    conn_context = connection_context.GetConnectionContext(
        args, flags.Product.RUN, self.ReleaseTrack()
    )
    service_ref = args.CONCEPTS.service.Parse()
    flags.ValidateResource(service_ref)

    with serverless_operations.Connect(conn_context) as client:
      service = client.GetService(service_ref)
      resource_change_validators.ValidateClearVpcConnector(service, args)
      has_latest = (
          service is None or traffic.LATEST_REVISION_KEY in service.spec_traffic
      )
      deployment_stages = stages.ServiceStages(
          include_iam_policy_set=False, include_route=has_latest
      )
      with progress_tracker.StagedProgressTracker(
          'Deploying...',
          deployment_stages,
          failure_message='Deployment failed',
          suppress_output=args.async_,
      ) as tracker:
        service = client.ReleaseService(
            service_ref,
            changes,
            tracker,
            asyn=args.async_,
            prefetch=service,
            generate_name=(
                flags.FlagIsExplicitlySet(args, 'revision_suffix')
                or flags.FlagIsExplicitlySet(args, 'tag')
            ),
        )

      if args.async_:
        pretty_print.Success(
            'Service [{{bold}}{serv}{{reset}}] is deploying '
            'asynchronously.'.format(serv=service.name)
        )
      else:
        service = client.GetService(service_ref)
        pretty_print.Success(
            messages_util.GetSuccessMessageForSynchronousDeploy(service)
        )
      return service


@base.ReleaseTracks(base.ReleaseTrack.BETA)
class BetaUpdate(Update):
  """Update Cloud Run environment variables and other configuration settings."""

  @staticmethod
  def Args(parser):
    Update.CommonArgs(parser)

    # Flags specific to managed CR
    managed_group = flags.GetManagedArgGroup(parser)
    flags.AddCustomAudiencesFlag(managed_group)
    flags.AddVpcNetworkGroupFlagsForUpdate(managed_group)


@base.ReleaseTracks(base.ReleaseTrack.ALPHA)
class AlphaUpdate(BetaUpdate):
  """Update Cloud Run environment variables and other configuration settings."""

  @classmethod
  def Args(cls, parser):
    Update.CommonArgs(parser, add_container_args=False)

    # Flags specific to managed CR
    managed_group = flags.GetManagedArgGroup(parser)
    flags.AddCustomAudiencesFlag(managed_group)
    flags.AddDefaultUrlFlag(managed_group)
    flags.AddVpcNetworkGroupFlagsForUpdate(managed_group)
    flags.AddRuntimeFlag(managed_group)
    flags.AddDescriptionFlag(managed_group)
    flags.AddServiceMinInstancesFlag(managed_group)
    flags.AddVolumesFlags(managed_group, cls.ReleaseTrack())
    # pylint: disable=protected-access
    flags.ContainerFlags(
        parser.parser._calliope_command,
        ContainerArgGroup(cls.ReleaseTrack()),
    ).AddToParser(managed_group)


AlphaUpdate.__doc__ = Update.__doc__
