with Arthexis.ORM;

package Apps.App.Functions.App_Functions is
   --  SQL-callable function registrations for the App backbone app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register App backbone SQL functions and helper views.
end Apps.App.Functions.App_Functions;
