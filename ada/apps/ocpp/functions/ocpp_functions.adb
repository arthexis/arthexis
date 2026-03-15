with Arthexis.ORM;

package body Apps.OCPP.Functions.OCPP_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS ocpp_transaction_energy AS "
         & "SELECT id, "
         & "CASE WHEN meter_stop_wh IS NULL THEN NULL "
         & "ELSE meter_stop_wh - meter_start_wh END AS energy_wh "
         & "FROM ocpp_transaction;");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS ocpp_charger_web_status AS "
         & "SELECT cp.serial_number, cp.status, cp.last_seen_utc, "
         & "COUNT(t.id) AS active_transactions "
         & "FROM ocpp_charge_point cp "
         & "LEFT JOIN ocpp_transaction t "
         & "ON t.charge_point_id = cp.id AND t.ended_at_utc IS NULL "
         & "GROUP BY cp.id, cp.serial_number, cp.status, cp.last_seen_utc "
         & "ORDER BY cp.serial_number;");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS ocpp_cli_simulator_targets AS "
         & "SELECT cp.id, cp.serial_number "
         & "FROM ocpp_charge_point cp "
         & "ORDER BY cp.serial_number;");
   end Install;

end Apps.OCPP.Functions.OCPP_Functions;
