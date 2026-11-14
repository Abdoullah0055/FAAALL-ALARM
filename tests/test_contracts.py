"""Les substituts simulés respectent le contrat que les adaptateurs d'Abdoullah doivent aussi respecter."""

from faaall_alarm.clock import ManualClock
from faaall_alarm.sms_simulated import SimulatedReplyInterpreter, SimulatedSmsGateway

from .contracts import assert_gateway_contract, assert_interpreter_contract


def test_passerelle_simulee_respecte_le_contrat():
    assert_gateway_contract(SimulatedSmsGateway(ManualClock()))


def test_interpreteur_simule_respecte_le_contrat():
    assert_interpreter_contract(SimulatedReplyInterpreter())
