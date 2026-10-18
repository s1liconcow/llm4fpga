"""Register the gyro's routed electrical-repair step, then use LibreLane's CLI."""
from pathlib import Path

from librelane.__main__ import cli
from librelane.steps import Step
from librelane.steps.step import CompositeStep
from librelane.state import DesignFormat
from librelane.steps.openroad import RepairDesignPostGRT, DetailedRouting, CheckAntennas, FillInsertion, RCX
from librelane.steps.odb import ReportDisconnectedPins
from librelane.steps.checker import TrDRC, DisconnectedPins


@Step.factory.register()
class GyroElectricalRepair(RepairDesignPostGRT):
    id = 'Gyro.ElectricalRepair'
    name = 'Gyro routed electrical and clock-wire repair'

    def get_script_path(self):
        return str(Path(__file__).with_name('electrical_repair.tcl'))


@Step.factory.register()
class GyroExtractedResize(RepairDesignPostGRT):
    id = 'Gyro.ExtractedResize'
    name = 'Repair electrical limits using extracted parasitics'
    inputs = [DesignFormat.ODB, DesignFormat.SPEF]

    def get_script_path(self):
        return str(Path(__file__).with_name('extracted_repair.tcl'))

    def prepare_env(self, env, state):
        env = super().prepare_env(env, state)
        for kind in ('min', 'nom', 'max'):
            env['GYRO_SPEF_' + kind.upper()] = str(state[DesignFormat.SPEF][kind + '_*'])
        return env


@Step.factory.register()
class GyroExtractedRepair(CompositeStep):
    id = 'Gyro.ExtractedRepair'
    name = 'Extracted-load electrical repair, reroute, and re-extraction'
    Steps = [GyroExtractedResize, DetailedRouting, CheckAntennas, TrDRC,
             ReportDisconnectedPins, DisconnectedPins, FillInsertion, RCX]


if __name__ == '__main__':
    cli()
