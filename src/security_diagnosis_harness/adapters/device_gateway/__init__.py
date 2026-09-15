"""DeviceGateway adapters。"""

from security_diagnosis_harness.adapters.device_gateway.contract_service import (
    create_contract_app,
)
from security_diagnosis_harness.adapters.device_gateway.cross_source import (
    CrossSourceCameraGateway,
)
from security_diagnosis_harness.adapters.device_gateway.http_security_platform import (
    SecurityPlatformHttpAdapter,
    SecurityPlatformHttpSettings,
)
from security_diagnosis_harness.adapters.device_gateway.onvif import (
    OnvifReadOnlyAdapter,
    OnvifReadOnlySettings,
)
from security_diagnosis_harness.adapters.device_gateway.registry import (
    InMemoryDeviceAdapterRegistry,
)
from security_diagnosis_harness.adapters.device_gateway.routed import (
    DeviceAssetDisabledError,
    DeviceCapabilityMissingError,
    DeviceRoutingError,
    RoutedDeviceGateway,
    RoutingContextRequiredError,
)
from security_diagnosis_harness.adapters.device_gateway.simulator import (
    SimulatorBehavior,
    SimulatorCallTrace,
    SimulatorDeviceGateway,
    SimulatorDirective,
    SimulatorScenario,
)
from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway

__all__ = [
    "DeviceAssetDisabledError",
    "DeviceCapabilityMissingError",
    "DeviceRoutingError",
    "CrossSourceCameraGateway",
    "InMemoryDeviceAdapterRegistry",
    "RoutedDeviceGateway",
    "RoutingContextRequiredError",
    "SecurityPlatformHttpAdapter",
    "OnvifReadOnlyAdapter",
    "OnvifReadOnlySettings",
    "SecurityPlatformHttpSettings",
    "SimulatorBehavior",
    "SimulatorCallTrace",
    "SimulatorDeviceGateway",
    "SimulatorDirective",
    "SimulatorScenario",
    "StaticDeviceGateway",
    "create_contract_app",
]
