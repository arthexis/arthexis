with Arthexis.ORM;

package Apps.Command.Functions.Command_Functions is
   --  SQL-callable function registrations for the Command app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register command helper views and seed command product actions.
end Apps.Command.Functions.Command_Functions;
