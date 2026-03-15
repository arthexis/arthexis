with Arthexis.ORM;

package Apps.OCPP.Functions.OCPP_Functions is
   """SQL-callable functions owned by the OCPP Ada app."""

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register OCPP SQL functions and helper views.
end Apps.OCPP.Functions.OCPP_Functions;
