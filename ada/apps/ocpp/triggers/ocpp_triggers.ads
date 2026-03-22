with Arthexis.ORM;

package Apps.OCPP.Triggers.OCPP_Triggers is
   --  SQLite trigger registrations for the OCPP Ada app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register OCPP model invariants.
end Apps.OCPP.Triggers.OCPP_Triggers;
