"""Zigbee2MQTT bridge over the core mqtt integration."""

from .addressing import is_z2m_group
from .classes import CLASSES, OTHER, capability
from .detect import detect
from .fade import Z2MCallResolver
from .get_attributes import Z2MGetAttributes
from .lower_brightness import Z2MLowerBrightness
from .lower_hue import Z2MLowerHue
from .lower_saturation import Z2MLowerSaturation
from .members import expand_group
from .raise_brightness import Z2MRaiseBrightness
from .raise_hue import Z2MRaiseHue
from .raise_saturation import Z2MRaiseSaturation
from .set_attributes import Z2MSetAttributes
from .stop import Z2MStop
from .turn_on_for import Z2MTurnOnFor
from ...core.scope import IntegrationInfo

INTEGRATION = IntegrationInfo(
    key="zigbee2mqtt",
    title="Zigbee2MQTT",
    ha_domain="mqtt",
    maintainers=("jasonmx",),
    detect=detect,
    is_group=is_z2m_group,
    expand_group=expand_group,
    capability=capability,
    classes=CLASSES,
    default_class=OTHER,
    call_resolver=Z2MCallResolver(),
    max_concurrency=4,
)

ADAPTERS = [
    Z2MGetAttributes,
    Z2MLowerBrightness,
    Z2MLowerHue,
    Z2MLowerSaturation,
    Z2MRaiseBrightness,
    Z2MRaiseHue,
    Z2MRaiseSaturation,
    Z2MSetAttributes,
    Z2MStop,
    Z2MTurnOnFor,
]
