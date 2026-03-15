package body Apps.OCPP.Views.OCPP_Views is

   function Active_Transactions_SQL return String is
   begin
      return
        "SELECT t.id, cp.serial_number, t.connector_id, t.started_at_utc "
        & "FROM ocpp_transaction t "
        & "JOIN ocpp_charge_point cp ON cp.id = t.charge_point_id "
        & "WHERE t.ended_at_utc IS NULL "
        & "ORDER BY t.started_at_utc DESC";
   end Active_Transactions_SQL;

   function CLI_Simulator_Targets_SQL return String is
   begin
      return
        "SELECT id, serial_number "
        & "FROM ocpp_cli_simulator_targets "
        & "ORDER BY serial_number";
   end CLI_Simulator_Targets_SQL;

   function Charger_Web_Status_SQL return String is
   begin
      return
        "SELECT serial_number, status, last_seen_utc, active_transactions "
        & "FROM ocpp_charger_web_status "
        & "ORDER BY serial_number";
   end Charger_Web_Status_SQL;

end Apps.OCPP.Views.OCPP_Views;
